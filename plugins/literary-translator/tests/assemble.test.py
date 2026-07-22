"""tests/assemble.test.py -- regression-lock / integration suite for
scripts/assemble.py, the W9 whole-book reconstruction script (see SKILL.md's
"W9 Assemble" section and references/assembly-and-output.md for the
authoritative spec this file was written against).

## Fixture strategy

Every test builds a REAL, self-contained ``durable_root`` on disk (manifest,
per-segment segpack/draft, the materialized ledger, canon.json, profile.yml
+ ownership marker) and invokes the ACTUAL ``assemble.py`` as a subprocess
-- ``python3 {durable_root}/scripts/assemble.py``, no CLI arguments, exactly
mirroring ``final_audit.py``'s own self-anchored, no-flag invocation (there
is no analogous per-segment CLI arg here -- assembly operates over the WHOLE
book). ``output_resolve.py`` and ``render_obsidian.py`` are copied in
alongside it as REAL files too (never stubs) since assemble.py's own last
step is resolving+calling the configured output adapter (contract §10) --
this is a genuine end-to-end integration test of the whole W9 chain, not
just assemble.py in isolation. The default fixture profile sets
``output.target: obsidian`` (the v1 primary target) for this reason.

## Two artifacts this file pins exactly (contract §5)

``${durable_root}/out/.assembled/nodestream.json`` and
``.../anchor_map.json`` have an EXACT, contractually-specified shape --
this file reads them directly off disk and asserts that shape precisely.
assemble.py's own stdout JSON line's *other* fields are deliberately NOT
schema-pinned here (the contract explicitly forbids inventing a new
``assembly-manifest.schema.json`` -- see §15's proportionality guardrails);
only the exit code and, where the contract is unambiguous, a bare
``success`` boolean are asserted.

## Documented interpretation calls (genuine contract ambiguities)

Where the shared contract leaves a behavior underspecified, this file picks
ONE concrete, defensible reading and tests it -- per the build brief's own
instruction ("if your reading and the implementers' diverge, that surfaces
a real contract ambiguity -- good, flag it"). Each call is documented at its
test:

  1. **Non-converged segment => FATAL (whole-project completeness gate).**
     A segment whose ledger status isn't "converged" is NOT quietly excluded
     from the book -- assemble.py refuses to assemble the whole project at
     all (exit 2, reason "project_incomplete"), because assembled_book is
     gated on the whole-project completeness predicate (SKILL.md "W9
     Assemble" / references/assembly-and-output.md Path 2's
     final-audit-summary.project_complete: true), re-derived cheaply from
     manifest + ledger (every manifest.segments[] unit -- body segments and
     translate-decision front/back matter alike -- must be converged) with
     no shell-out. This file's original reading ("SKIP, not fatal --
     render+diff is the real completeness gate, not assemble.py itself") was
     WRONG: diff_rendered_output.py only checks that a re-render still
     matches a saved BASELINE, so baselining a partial book (one missing
     segments) simply passes forever and never catches the missing segments
     -- completeness has to be enforced at assembly time, by assemble.py
     itself, not deferred to render+diff.
  2. **draft-sha1 mismatch (stale review) => FATAL for the whole run.**
     Unlike a merely-non-converged segment, a hand-edit the reviewer never
     saw is the exact defect class final_audit.py's own hard check 2 (see
     final_audit.test.py) treats as a hard failure -- "must not silently
     ship" (contract §3) is read here as stronger than an ordinary
     incompleteness, so this is exit 1, not a quiet skip.
  3. **Monotonic order_index violation => surfaced, exit code not asserted
     either way.** The contract literally says "warn/fail on violation" --
     genuinely undecided. This file only asserts the anomaly is DETECTED
     and NAMED somewhere in the process output, not which exit code it maps
     to.
  4. **frontback decision:"regenerate" placeholder node's exact `kind`/
     `raw_type`.** The NodeStream contract's own `kind` enum is closed to
     {heading,prose,verse} (§5) -- a synthesized placeholder must therefore
     reuse one of those three, which one is unspecified. This file only
     asserts a node exists for the frontback id, carries non-empty
     placeholder text, and that a WARN is emitted naming it and
     "regenerate" -- not which `kind` value was chosen.
  5. **Nested sentinel handling inside a footnote's own def text.**
     CONFIRMED (not actually ambiguous once read against the real
     assemble.py's own module docstring): EVERY sentinel found inside a
     footnote's own def text -- a nested ⟦FNREF_N⟧ AND a verse embedded via
     verse.store context:"footnote" alike -- is uniformly STRIPPED (never
     recursively expanded, never independently resolved) when building the
     book-wide `footnotes[]` array. This file's original draft guessed a
     more nuanced split (nested FNREF stripped, but an embedded verse with
     its own resolution data resolved-and-deferred); the real implementation
     picked the simpler, uniform "strip everything" reading instead, which
     its own docstring states explicitly -- test 6 below asserts the actual,
     confirmed behavior.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
)

ASSEMBLE_SRC = SCRIPTS_SRC_DIR / "assemble.py"
OUTPUT_RESOLVE_SRC = SCRIPTS_SRC_DIR / "output_resolve.py"
RENDER_OBSIDIAN_SRC = SCRIPTS_SRC_DIR / "render_obsidian.py"
# assemble.py imports validate_draft.py as a sibling (profile loading, via
# vd.load_profile()) -- must be copied alongside it in every fixture root.
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"

assert ASSEMBLE_SRC.is_file(), (
    f"assemble.py not found at {ASSEMBLE_SRC} -- Phase 0 (contract §4-§7) "
    "has not landed yet"
)
assert OUTPUT_RESOLVE_SRC.is_file(), f"output_resolve.py not found at {OUTPUT_RESOLVE_SRC}"
assert RENDER_OBSIDIAN_SRC.is_file(), f"render_obsidian.py not found at {RENDER_OBSIDIAN_SRC}"
assert VALIDATE_DRAFT_SRC.is_file(), f"validate_draft.py not found at {VALIDATE_DRAFT_SRC}"

FN_PH_1 = "⟦FNREF_1⟧"
FN_PH_2 = "⟦FNREF_2⟧"
V_PH_A = "⟦VERSE_vA_abc12345⟧"

# All 15 cache_key fields (see cache_key.py's own CACHE_KEY_FIELD_ORDER) --
# assemble.py's own gate only reads status/reviewed_draft_sha1 per the
# contract, but a ledger.schema.json-shaped fixture keeps this file honest
# in case assemble.py (or a future jsonschema hardening pass) validates the
# whole record.
DUMMY_CACHE_KEY = {
    "input_sha1": "a" * 40,
    "style_contract_hash": "b" * 40,
    "used_terms_hash": "c" * 40,
    "pipeline_version": "v1",
    "schema_hash": "d" * 40,
    "prompt_hash": "e" * 40,
    "agent_config_hash": "f" * 40,
    "profile_semantics_hash": "0" * 40,
    "particle_config_hash": "1" * 40,
    "source_extraction_hash": "2" * 40,
    "source_input_hash": "3" * 40,
    "derivation_bundle_hash": "4" * 40,
    "verse_map_hash": "5" * 40,
    "note_map_hash": "6" * 40,
    "plugin_bundle_hash": "7" * 40,
}


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def default_profile(verse_mode="full_rhymed_plus_literal", output_target="obsidian", custom_renderer_path=None,
                     mentions_enabled: "bool | None" = False):
    """`mentions_enabled`: three-state, mirroring the real
    `output.adapter_config.obsidian.mentions_section` shape a profile.yml
    can take -- `False` (the default here) writes an EXPLICIT
    `enabled: false`, so the ~60 tests in this file that assemble a book
    and have nothing to do with the Mentions feature stay unaffected by
    the 1.10.0 default-on flip (make_root()'s scripts/ dir deliberately
    under-provisions bootstrap_names.py/canon_senses.py/
    occurrence_targets.py, §3.8 -- an IMPLIED/explicit-true mentions flag
    on that root now fails closed, unconditionally, per the fail-closed-
    always fix; a test that isn't actually exercising that must not
    silently inherit it). `True` writes an explicit `enabled: true`.
    `None` OMITS the `mentions_section` key entirely -- the genuinely
    IMPLIED shape, used only by the dedicated fail-closed tests below that
    need to prove the implied case specifically."""
    obsidian_cfg = {"folders": {}}
    if mentions_enabled is not None:
        obsidian_cfg["mentions_section"] = {"enabled": mentions_enabled}
    return {
        "profile_version": 1,
        "project": {
            "title": "Test Book",
            "durable_root": "/placeholder",  # overwritten below with the real root
            "pipeline_version": "v1",
            "max_segment_words": 15000,
        },
        "source": {
            "format": "plain_text",
            "path": "/logical/source.txt",
            "gutenberg_id": None,
            "language": {
                "code": "fr",
                "particle_config": "fr_test.json",
                "smoke_test": {"report_path": None},
            },
            "adapter_config": {
                "gutenberg_epub": None,
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
        "target": {"language": {"code": "ru", "register_notes": "informal"}},
        "verse_policy": {"mode": verse_mode, "threshold_lines": None},
        "engine": {"effort": "high", "max_fix_rounds": 4, "batch_agent_cap": 1000},
        "footnotes": {"apparatus_policy": "translate_all"},
        "glossary": {"research_mode": "live"},
        "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
        "output": {
            "v1_scope": "assembled_book",
            "destination": "/placeholder/out/",
            "target": output_target,
            "name_display": {"parenthetical_originals": "never"},
            "index": {"enabled": False, "person_grouping": False},
            "adapter_config": {
                "obsidian": obsidian_cfg, "epub": None,
                "custom": {"renderer_path": custom_renderer_path} if custom_renderer_path else None,
            },
        },
    }


def make_root(
    tmp_path, verse_mode="full_rhymed_plus_literal", output_target="obsidian", custom_renderer_path=None,
    mentions_enabled: "bool | None" = False,
) -> Path:
    """A bare durable_root: real copies of assemble.py + its two sibling
    scripts, profile.yml + ownership marker, an empty canon.json. Manifest /
    segpack / draft / ledger content is written per-test by the helpers
    below -- this mirrors final_audit.test.py's split between a bare
    make_durable_root() and per-segment add_converged_segment().
    `mentions_enabled`: forwarded to `default_profile()` -- see its own
    docstring for the three-state (False/True/None) shape. Defaults to
    explicit `False` here too, for the same reason: this fixture's
    scripts/ dir deliberately never copies bootstrap_names.py/
    canon_senses.py/occurrence_targets.py (§3.8), so an implied or
    explicit-true Mentions flag now fails closed unconditionally -- most
    callers of make_root() have nothing to do with that feature."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC, VALIDATE_DRAFT_SRC):
        shutil.copy2(src, scripts_dir / src.name)

    profile = default_profile(
        verse_mode=verse_mode, output_target=output_target, custom_renderer_path=custom_renderer_path,
        mentions_enabled=mentions_enabled,
    )
    profile["project"]["durable_root"] = str(root)
    profile["output"]["destination"] = str(root / "out")
    (root / "profile.yml").write_text(_yaml_dump(profile), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )

    (root / "canon.json").write_text(
        json.dumps(
            {
                "entries": {},
                "review_queue": [],
                "generation_hashes": {"particle_config_hash": "x", "derivation_bundle_hash": "y"},
            }
        ),
        encoding="utf-8",
    )

    (root / "segments").mkdir()
    (root / "runs").mkdir()
    return root


def _yaml_dump(obj) -> str:
    import yaml

    return yaml.safe_dump(obj, sort_keys=False)


def write_manifest(root, blocks, segments, footnotes=None, verse_store=None, frontback=None,
                    heading_types=None, heading_levels=None):
    """blocks: dict[id -> block dict, WITHOUT 'id' key (filled in here)].
    segments: list of segment dicts (each already fully shaped).
    heading_types: optional list of manifest-declared heading block-type
    tags (#210) -- omitted entirely (not even as an empty list) unless a
    caller passes one, so the default fixture stays byte-identical to
    pre-#210 manifests and exercises the schema's back-compat "absent"
    path.
    heading_levels: optional dict[raw_type -> level int] (#210 R1) --
    likewise omitted entirely unless a caller passes one, for the same
    back-compat-default reason."""
    for bid, b in blocks.items():
        b.setdefault("id", bid)
        b.setdefault("sha1", hashlib.sha1(bid.encode()).hexdigest())
        b.setdefault("source_file", "source.txt")
    manifest = {
        "blocks": blocks,
        # Deliberately scrambled/reversed vs. the real reading order -- a
        # red herring assemble.py must never consult (contract §14).
        "spine": [
            {"pos": 0, "file": "zzz_last.txt", "klass": "body"},
            {"pos": 1, "file": "aaa_first.txt", "klass": "body"},
        ],
        "segments": segments,
        "footnotes": footnotes or [],
        "frontback": frontback or [],
        "verse": {"store": verse_store or []},
        "source_inputs": ["source.txt"],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    if heading_types is not None:
        manifest["heading_types"] = heading_types
    if heading_levels is not None:
        manifest["heading_levels"] = heading_levels
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def write_segpack(root, seg, blocks, footnotes=None, verses=None):
    pack = {
        "seg": seg,
        "title": seg,
        "kind": "body",
        "word_count": 10,
        "blocks": blocks,
        "footnotes": footnotes or [],
        "verses": verses or [],
        "names": [],
        "canon_names": [],
        "new_names": [],
        "generation_hashes": {
            "source_extraction_hash": "x",
            "source_input_hash": "y",
            "particle_config_hash": "x",
            "derivation_bundle_hash": "y",
        },
    }
    (root / "segments" / f"segpack_{seg}.json").write_text(
        json.dumps(pack, ensure_ascii=False), encoding="utf-8"
    )


def draft_content_sha1_of(doc: dict) -> str:
    """1.2.0: ledger_update.py/draft_sha1.py/assemble.py all hash a segment
    draft's CONTENT, not its raw on-disk bytes -- CONTRACT-1.2.0-
    reliability.md section 2. Independent, stdlib-only ground truth (drop
    'dispatch_token' if present, sha1 the sorted-key canonical
    re-serialization); duplicated here rather than imported, matching this
    suite's "each test file stays self-contained" convention (see
    tests/draft_sha1.test.py's own canonical_expected_sha1() for the
    more-exhaustively-tested original)."""
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def write_draft(root, seg, blocks, footnotes=None, verses=None, names=None, notes=None) -> bytes:
    draft = {
        "seg": seg,
        "blocks": blocks,
        "footnotes": footnotes or {},
        "verses": verses or {},
        "names": names or [],
        "notes": notes or [],
    }
    draft_bytes = json.dumps(draft, ensure_ascii=False).encode("utf-8")
    (root / "segments" / f"{seg}.draft.json").write_bytes(draft_bytes)
    return draft_bytes


def write_ledger(root, entries: dict) -> None:
    """entries: seg -> {"status": ..., "reviewed_draft_sha1_override": optional}.
    reviewed_draft_sha1 auto-computed from the on-disk draft -- via the same
    canonical draft_content_sha1() algorithm the real ledger_update.py/
    assemble.py use, NOT a raw-bytes hash of the file -- unless a literal
    override is supplied (used to simulate a post-review hand-edit)."""
    segments = {}
    for seg, cfg in entries.items():
        record = {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "status": cfg["status"],
        }
        if cfg["status"] == "converged":
            draft_path = root / "segments" / f"{seg}.draft.json"
            draft_doc = json.loads(draft_path.read_text(encoding="utf-8"))
            sha1 = cfg.get("reviewed_draft_sha1_override") or draft_content_sha1_of(draft_doc)
            record.update(
                rounds=1,
                cache_key=DUMMY_CACHE_KEY,
                n_blocks=1,
                n_footnotes=0,
                n_verses=0,
                reviewed_draft_sha1=sha1,
            )
        else:
            record["reason"] = cfg.get("reason", "test fixture")
        segments[seg] = record
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8"
    )


def run_assemble(root: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "assemble.py")],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_one_json_line(proc: subprocess.CompletedProcess) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one stdout JSON line, got {len(lines)}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(lines[0])


def read_nodestream(root: Path) -> dict:
    path = root / "out" / ".assembled" / "nodestream.json"
    assert path.is_file(), f"expected nodestream.json artifact at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def read_anchor_map(root: Path) -> dict:
    path = root / "out" / ".assembled" / "anchor_map.json"
    assert path.is_file(), f"expected anchor_map.json artifact at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# A clean, two-segment baseline book shared by several tests below.
#
#   seg01: p1 (html prose, FNREF_1) -> p2 (plain prose) -> vblockA (verse)
#          FN1 def block exists in manifest.blocks{} but is NOT in seg01's
#          block_ids -- footnote-def-never-inline (contract §4.4).
#   seg02: p3 (plain prose) -> p4 (plain prose)
#
# manifest.blocks{} order_index is the single GLOBAL axis: p1=0, p2=1,
# vblockA=2, FN1=3, p3=4, p4=5. Each segpack's OWN order_index resets to
# 0-based PER SEGMENT (segpack.schema.json's documented "local to this
# segment" rank) -- seg02's segpack blocks are 0,1 even though their global
# manifest order_index is 4,5. A naive assembler that stitched by segpack
# order_index instead of manifest segment-array-order + block_ids-order
# would interleave seg02's blocks with seg01's (both start at 0) --
# test_whole_book_order_ignores_per_segment_local_segpack_order_index below
# is the regression lock for exactly that bug class (contract §14).
# ---------------------------------------------------------------------------


def build_clean_two_segment_book(root: Path, verse_mode="full_rhymed_plus_literal"):
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                    "source_html": f"<p>Some prose {FN_PH_1} attached.</p>",
                    "plain_text": f"Some prose {FN_PH_1} attached.", "fnrefs": [1]},
            "p2": {"type": "PARA", "seg": "seg01", "order_index": 1,
                    "plain_text": "Plain prose two."},
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 2,
                        "plain_text": V_PH_A},
            "FN1": {"type": "FN", "seg": None, "order_index": 3,
                    "plain_text": "French footnote definition text."},
            "p3": {"type": "PARA", "seg": "seg02", "order_index": 4,
                   "plain_text": "Third block text."},
            "p4": {"type": "PARA", "seg": "seg02", "order_index": 5,
                   "plain_text": "Fourth block text."},
        },
        segments=[
            {"seg": "seg01", "kind": "body", "title_text": "Chapter One",
             "block_ids": ["p1", "p2", "vblockA"], "word_count": 100},
            {"seg": "seg02", "kind": "body", "title_text": "Chapter Two",
             "block_ids": ["p3", "p4"], "word_count": 50},
        ],
        footnotes=[{"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"}],
        verse_store=[{"vid": "vA", "placeholder": V_PH_A, "context": "body",
                       "parent_block": "vblockA", "mount": "block"}],
    )
    write_segpack(
        root, "seg01",
        blocks=[
            {"id": "p1", "order_index": 0, "source_html": f"<p>Some prose {FN_PH_1} attached.</p>"},
            {"id": "p2", "order_index": 1, "plain_text": "Plain prose two."},
            {"id": "vblockA", "order_index": 2, "source_html": "<p>Verse line one<br/>Verse line two</p>"},
        ],
        footnotes=[{"n": 1, "source_text": "French footnote definition text."}],
        verses=[{"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"}],
    )
    write_segpack(
        root, "seg02",
        blocks=[
            {"id": "p3", "order_index": 0, "plain_text": "Third block text."},
            {"id": "p4", "order_index": 1, "plain_text": "Fourth block text."},
        ],
    )
    verses_payload = {"vA": {"rendered": "Line one\nLine two", "literal_gloss": "Gloss for one and two"}}
    if verse_mode == "skip":
        verses_payload = {"vA": {}}
    write_draft(
        root, "seg01",
        blocks={"p1": f"Translated prose one {FN_PH_1} done.", "p2": "Translated prose two.",
                "vblockA": V_PH_A},
        footnotes={"1": "Translated footnote text."},
        verses=verses_payload,
    )
    write_draft(
        root, "seg02",
        blocks={"p3": "Translated block three.", "p4": "Translated block four."},
    )
    write_ledger(root, {"seg01": {"status": "converged"}, "seg02": {"status": "converged"}})


# ===========================================================================
# 1. Whole-book reading order: segment ARRAY order + per-segment block_ids
#    order is the sole axis. spine[] (reversed) and per-segment-local segpack
#    order_index (both reset to 0 per segment) must never be consulted.
# ===========================================================================


def test_whole_book_order_follows_manifest_segments_and_block_ids_order(tmp_path):
    root = make_root(tmp_path)
    build_clean_two_segment_book(root)

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    ns = read_nodestream(root)
    assert ns["book"]["seg_order"] == ["seg01", "seg02"]
    node_ids = [n["id"] for n in ns["nodes"]]
    # p1, p2, vblockA (seg01, in block_ids order) then p3, p4 (seg02) --
    # FN1 (a footnote-def block) never appears (see test 5 below).
    assert node_ids == ["p1", "p2", "vblockA", "p3", "p4"]


def test_whole_book_order_ignores_per_segment_local_segpack_order_index(tmp_path):
    """seg02's segpack blocks carry LOCAL order_index 0,1 (per
    segpack.schema.json's own "rank within this segment" semantics) -- the
    exact same local values as seg01's own first two blocks. If assemble.py
    mistakenly stitched the whole book by concatenating+sorting on segpack
    order_index rather than manifest segment-array-order + block_ids-order,
    seg02's blocks would sort back in among seg01's, not stay in their own
    segment's own place at the tail of the book."""
    root = make_root(tmp_path)
    build_clean_two_segment_book(root)

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    ns = read_nodestream(root)
    node_ids = [n["id"] for n in ns["nodes"]]
    assert node_ids.index("p3") > node_ids.index("vblockA"), (
        "seg02's blocks must follow ALL of seg01's blocks -- got order "
        f"{node_ids} (looks like per-segment-local segpack order_index was "
        "used as the whole-book stitch key instead of manifest order)"
    )


def test_spine_order_is_never_consulted(tmp_path):
    """The fixture's own spine[] is deliberately reversed/unrelated to the
    real reading order (contract §14: spine[].pos is a RED HERRING) -- the
    clean two-segment book's correct order (test 1 above) already proves
    this implicitly, but this test makes the invariant explicit and
    self-documenting."""
    root = make_root(tmp_path)
    build_clean_two_segment_book(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["spine"][0]["file"] == "zzz_last.txt", "sanity: spine is scrambled"

    result = run_assemble(root)
    assert result.returncode == 0

    ns = read_nodestream(root)
    node_ids = [n["id"] for n in ns["nodes"]]
    assert node_ids == ["p1", "p2", "vblockA", "p3", "p4"]


# ===========================================================================
# 2. FN:{N} definition blocks are never inlined into the book.
# ===========================================================================


def test_footnote_def_block_never_appears_as_an_inline_node(tmp_path):
    root = make_root(tmp_path)
    build_clean_two_segment_book(root)

    result = run_assemble(root)
    assert result.returncode == 0

    ns = read_nodestream(root)
    node_ids = {n["id"] for n in ns["nodes"]}
    assert "FN1" not in node_ids, (
        "a footnote definition block must never be rendered as an inline "
        "book node -- it surfaces only via the footnotes[] table"
    )
    footnote_ns = {fn["n"] for fn in ns["footnotes"]}
    assert footnote_ns == {1}
    assert ns["footnotes"][0]["text"] == "Translated footnote text."


# ===========================================================================
# 3. Anchor-map completeness (contract §5).
# ===========================================================================


def test_anchor_map_mirrors_nodes_and_lists_every_footnote_and_verse(tmp_path):
    root = make_root(tmp_path)
    build_clean_two_segment_book(root)

    result = run_assemble(root)
    assert result.returncode == 0

    ns = read_nodestream(root)
    am = read_anchor_map(root)

    assert [b["block_id"] for b in am["blocks"]] == [n["id"] for n in ns["nodes"]], (
        "anchor_map.blocks must mirror nodestream.nodes 1:1, in the same order"
    )
    for anchor, node in zip(am["blocks"], ns["nodes"]):
        assert anchor["seg"] == node["seg"]
        assert anchor["kind"] == node["kind"]
        assert anchor["order_index"] == node["order_index"]
    assert am["footnotes"] == [1]
    assert am["verses"] == ["vA"]


# ===========================================================================
# 4. medium: html vs plain.
# ===========================================================================


def test_medium_is_html_when_source_html_present_plain_otherwise(tmp_path):
    root = make_root(tmp_path)
    build_clean_two_segment_book(root)

    result = run_assemble(root)
    assert result.returncode == 0

    ns = read_nodestream(root)
    by_id = {n["id"]: n for n in ns["nodes"]}
    assert by_id["p1"]["medium"] == "html", "p1's manifest block carries source_html"
    assert by_id["p3"]["medium"] == "plain", "p3's manifest block carries plain_text only"


# ===========================================================================
# 5. Fail-closed sentinel resolution (contract §6).
# ===========================================================================


def test_dangling_fnref_is_fatal(tmp_path):
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                   "plain_text": "Some text ⟦FNREF_99⟧ here.", "fnrefs": [99]},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
        footnotes=[],  # n=99 never defined anywhere
    )
    write_segpack(root, "seg01", blocks=[
        {"id": "p1", "order_index": 0, "plain_text": "Some text ⟦FNREF_99⟧ here."},
    ])
    write_draft(root, "seg01", blocks={"p1": "Translated text ⟦FNREF_99⟧ here."})
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert "99" in payload["error"], "the offending footnote number should be named"
    assert not (root / "out" / ".assembled" / "nodestream.json").is_file(), (
        "a fatal fail-closed error must not leave a partial nodestream.json artifact"
    )


def test_unknown_verse_placeholder_is_fatal(tmp_path):
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 0,
                        "plain_text": V_PH_A},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["vblockA"], "word_count": 10}],
        verse_store=[{"vid": "vA", "placeholder": V_PH_A, "context": "body",
                       "parent_block": "vblockA", "mount": "block"}],
    )
    write_segpack(root, "seg01", blocks=[
        {"id": "vblockA", "order_index": 0, "plain_text": V_PH_A},
    ], verses=[{"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"}])
    # Draft contains a BOGUS placeholder string never registered in segpack.verses.
    write_draft(root, "seg01", blocks={"vblockA": "⟦VERSE_unknownvid_deadbeef⟧"})
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert "deadbeef" in payload["error"] or "unknownvid" in payload["error"]


def test_duplicate_book_wide_footnote_n_is_fatal(tmp_path):
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                   "plain_text": f"Text {FN_PH_1} here.", "fnrefs": [1]},
            "FN1a": {"type": "FN", "seg": None, "order_index": 1, "plain_text": "First def."},
            "FN1b": {"type": "FN", "seg": None, "order_index": 2, "plain_text": "Duplicate-n def."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
        # Same n=1 recorded twice against two DIFFERENT def blocks -- a
        # violation of "n is unique book-wide" (contract §3/§6).
        footnotes=[
            {"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1a"},
            {"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1b"},
        ],
    )
    write_segpack(root, "seg01", blocks=[
        {"id": "p1", "order_index": 0, "plain_text": f"Text {FN_PH_1} here."},
    ], footnotes=[{"n": 1, "source_text": "First def."}])
    write_draft(root, "seg01", blocks={"p1": f"Translated {FN_PH_1} here."},
                footnotes={"1": "Translated def."})
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert "duplicate" in payload["error"].lower() and "n=1" in payload["error"]


def test_verse_skip_mode_empty_content_is_not_an_error(tmp_path):
    """Under verse_policy.mode: skip, draft.verses[vid] == {} is the
    documented, intentional shape (contract §6) -- must assemble cleanly,
    never mistaken for a missing/dangling verse."""
    root = make_root(tmp_path, verse_mode="skip")
    build_clean_two_segment_book(root, verse_mode="skip")

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    ns = read_nodestream(root)
    by_id = {n["id"]: n for n in ns["nodes"]}
    verse_node = by_id["vblockA"]
    assert verse_node["kind"] == "verse"
    # The verse's own content is legitimately empty under skip -- no crash,
    # no fatal, no dangling-placeholder complaint.
    matching = [v for v in verse_node["verses"] if v["vid"] == "vA"]
    assert len(matching) == 1
    assert matching[0]["content"] == {}


# ===========================================================================
# 6. Nested-sentinel handling inside a footnote's OWN def text (see this
#    file's module docstring, ambiguity #5, for the two-cases reading).
# ===========================================================================


def test_nested_fnref_inside_a_footnote_def_is_stripped(tmp_path):
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                   "plain_text": f"Body text {FN_PH_1} here.", "fnrefs": [1]},
            "FN1": {"type": "FN", "seg": None, "order_index": 1,
                    "plain_text": f"See also {FN_PH_2} for more."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
        footnotes=[{"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"}],
    )
    write_segpack(root, "seg01", blocks=[
        {"id": "p1", "order_index": 0, "plain_text": f"Body text {FN_PH_1} here."},
    ], footnotes=[{"n": 1, "source_text": f"See also {FN_PH_2} for more."}])
    write_draft(
        root, "seg01",
        blocks={"p1": f"Translated body {FN_PH_1} here."},
        footnotes={"1": f"See also {FN_PH_2} for more (translated)."},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    ns = read_nodestream(root)
    fn1_text = ns["footnotes"][0]["text"]
    assert FN_PH_2 not in fn1_text, (
        f"a nested ⟦FNREF_2⟧ inside footnote 1's own def text must be "
        f"stripped, not left inline nor recursively expanded -- got {fn1_text!r}"
    )


def test_verse_embedded_in_a_footnote_def_is_also_stripped(tmp_path):
    """A verse whose verse.store entry parents to a FOOTNOTE def block
    (context:"footnote", mount:"embedded") is, per the CONFIRMED real
    behavior (see this file's module docstring, interpretation #5), stripped
    from the footnote's book-wide text the SAME uniform way a nested FNREF
    is -- never independently resolved/deferred. This must not raise a
    fatal error either (footnote def text is never itself run through the
    fail-closed per-block sentinel gate -- only body-block text is).

    CONFIRMED REGRESSION (introduced by FIXSPEC B1's orphan_verse fix, flag
    to the lead for B): seg_referenced_vids is only ever populated by
    scanning BODY block text (the block_ids loop) -- a verse whose ONLY
    placeholder occurrence is inside a footnote's own def text (this exact,
    previously-passing scenario) now FATALLY misfires the new orphan_verse
    check, even though assemble.py's own module docstring still explicitly
    describes "an embedded verse inside a footnote,
    verse.store[].context == 'footnote'" as a real, supported case. The
    orphan-check's referenced-set needs to ALSO count a vid whose
    placeholder appears inside any draft_footnotes[n] text this segment's
    blocks reference, not just body-block text. This test intentionally
    still asserts the ORIGINAL, documented, correct behavior (exit 0,
    stripped) and is expected to stay red until that interaction is fixed."""
    v_ph = "⟦VERSE_vFoot_cafe1234⟧"
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                   "plain_text": f"Body text {FN_PH_1} here.", "fnrefs": [1]},
            "FN1": {"type": "FN", "seg": None, "order_index": 1,
                    "plain_text": f"A cited couplet: {v_ph}"},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
        footnotes=[{"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"}],
        verse_store=[{"vid": "vFoot", "placeholder": v_ph, "context": "footnote",
                       "parent_block": "FN1", "mount": "embedded"}],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "p1", "order_index": 0, "plain_text": f"Body text {FN_PH_1} here."}],
        footnotes=[{"n": 1, "source_text": f"A cited couplet: {v_ph}"}],
        verses=[{"vid": "vFoot", "placeholder": v_ph, "parent_block": "FN1"}],
    )
    write_draft(
        root, "seg01",
        blocks={"p1": f"Translated body {FN_PH_1} here."},
        footnotes={"1": f"A cited couplet: {v_ph}"},
        verses={"vFoot": {"rendered": "Rendered line one\nRendered line two",
                           "literal_gloss": "Gloss line one and two"}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    ns = read_nodestream(root)
    fn1_text = ns["footnotes"][0]["text"]
    assert v_ph not in fn1_text, (
        f"an embedded verse placeholder inside a footnote's own def text "
        f"must be stripped, same as a nested FNREF -- got {fn1_text!r}"
    )


def test_footnote_cited_in_verse_content_whose_def_embeds_a_verse_is_not_orphan(tmp_path):
    """r6 finding 2 -- a near-clone of test_verse_embedded_in_a_footnote_def_is_also_stripped
    above, with the ⟦FNREF_1⟧ citation moved OUT of p1's own block text and
    INTO an embedded verse's (vA) translated content instead: p1's block text
    now carries only vA's placeholder, and vA's own draft.verses content
    (rendered) is the one that cites ⟦FNREF_1⟧. Footnote 1's own definition
    (FN1) in turn embeds a SECOND verse (vFoot, mount=embedded,
    context=footnote) -- exactly like the original test. Pre-fix (no
    verse-content FNREF scan at all), footnote 1 is never referenced anywhere
    in this segment and this fatals (orphan_footnote_def, since
    seg_referenced_ns never sees n=1); post-fix, _scan_verse_content_fnrefs
    registers n=1 from vA's own content AND (the r6 finding-2 inline splice)
    marks vFoot referenced too, so orphan_verse doesn't false-fatal it
    either. REGRESSION-class (git-stash the fix -> confirm RED)."""
    v_ph_a = "⟦VERSE_vA_abc12345⟧"
    v_ph_foot = "⟦VERSE_vFoot_cafe1234⟧"
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                   "plain_text": f"Body text {v_ph_a} here."},
            "FN1": {"type": "FN", "seg": None, "order_index": 1,
                    "plain_text": f"A cited couplet: {v_ph_foot}"},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
        footnotes=[{"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"}],
        verse_store=[
            {"vid": "vA", "placeholder": v_ph_a, "context": "body",
             "parent_block": "p1", "mount": "embedded"},
            {"vid": "vFoot", "placeholder": v_ph_foot, "context": "footnote",
             "parent_block": "FN1", "mount": "embedded"},
        ],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "p1", "order_index": 0, "plain_text": f"Body text {v_ph_a} here."}],
        footnotes=[{"n": 1, "source_text": f"A cited couplet: {v_ph_foot}"}],
        verses=[
            {"vid": "vA", "placeholder": v_ph_a, "parent_block": "p1"},
            {"vid": "vFoot", "placeholder": v_ph_foot, "parent_block": "FN1"},
        ],
    )
    write_draft(
        root, "seg01",
        # p1's OWN block text carries only vA's placeholder -- no ⟦FNREF_1⟧
        # here at all; the footnote citation lives inside vA's own content.
        blocks={"p1": f"Translated body {v_ph_a} here."},
        footnotes={"1": f"A cited couplet: {v_ph_foot}"},
        verses={
            "vA": {"rendered": f"Rendered line {FN_PH_1}", "literal_gloss": "Gloss line, no ref"},
            "vFoot": {"rendered": "Rendered embedded couplet", "literal_gloss": "Gloss embedded couplet"},
        },
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    ns = read_nodestream(root)
    by_id = {n["id"]: n for n in ns["nodes"]}
    assert 1 in by_id["p1"]["fnrefs"], (
        "footnote 1, cited only inside vA's own verse content, must be merged "
        "into the carrier block node's fnrefs"
    )
    fn_ns = {fn["n"] for fn in ns["footnotes"]}
    assert 1 in fn_ns, "footnote 1 must be in the book-wide footnotes[] table"
    fn1_text = next(fn["text"] for fn in ns["footnotes"] if fn["n"] == 1)
    assert v_ph_foot not in fn1_text, (
        "the inner verse placeholder embedded in footnote 1's own def text "
        "must still be stripped (Phase 0 policy), same as the un-nested case"
    )
    all_verse_vids = {v["vid"] for n in ns["nodes"] for v in n["verses"]}
    assert "vA" in all_verse_vids
    assert "vFoot" not in all_verse_vids, (
        "the inner verse (referenced only via the footnote-def embed) must "
        "never be independently resolved into any node's own verses[] -- "
        "referenced-only, never rendered"
    )


def test_footnote_cited_twice_in_one_block_is_duplicate(tmp_path):
    """r7 finding 2 -- a defensive pin of the UNTOUCHED block-branch
    invariant _scan_verse_content_fnrefs's own docstring says it deliberately
    mirrors rather than shares: a single block whose OWN text cites
    ⟦FNREF_1⟧ TWICE is still duplicate_footnote_ref, exercising the
    ORIGINAL per-occurrence within-block duplicate pass (unchanged by adding
    the verse-content scan alongside it). Must pass on pristine AND after the
    verse-content changes -- a byte-compat pin, not a regression test."""
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                   "plain_text": f"{FN_PH_1} middle {FN_PH_1}", "fnrefs": [1]},
            "FN1": {"type": "FN", "seg": None, "order_index": 1, "plain_text": "The definition."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
        footnotes=[{"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"}],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "p1", "order_index": 0, "plain_text": f"{FN_PH_1} middle {FN_PH_1}"}],
        footnotes=[{"n": 1, "source_text": "The definition."}],
    )
    write_draft(
        root, "seg01",
        blocks={"p1": f"{FN_PH_1} middle {FN_PH_1}"},
        footnotes={"1": "The translated definition."},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert (
        payload.get("reason") == "duplicate_footnote_ref"
        or "duplicate" in payload["error"].lower()
    )


def test_verse_fnref_in_both_fields_deduped_across_fields(tmp_path):
    """r6 finding 4 -- a footnote cited naturally in BOTH a verse's rhymed
    `rendered` and its `literal_gloss` (full_rhymed_plus_literal mode) is ONE
    citation, not a duplicate: _scan_verse_content_fnrefs dedups ACROSS
    fields. RED against an unconditional-union/combined-field scan that
    treats the two fields as one combined string (that reading would trip
    duplicate_footnote_ref on this exact, legitimate shape)."""
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 0,
                        "plain_text": V_PH_A},
            "FN1": {"type": "FN", "seg": None, "order_index": 1, "plain_text": "The definition."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["vblockA"], "word_count": 10}],
        footnotes=[{"n": 1, "anchor_block": "vblockA", "anchor_seg": "seg01", "def_block": "FN1"}],
        verse_store=[{"vid": "vA", "placeholder": V_PH_A, "context": "body",
                       "parent_block": "vblockA", "mount": "block"}],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "vblockA", "order_index": 0, "plain_text": V_PH_A}],
        footnotes=[{"n": 1, "source_text": "The definition."}],
        verses=[{"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"}],
    )
    write_draft(
        root, "seg01",
        blocks={"vblockA": V_PH_A},
        footnotes={"1": "The translated definition."},
        verses={"vA": {"rendered": f"Rhymed line {FN_PH_1}",
                       "literal_gloss": f"Literal gloss {FN_PH_1}"}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    ns = read_nodestream(root)
    by_id = {n["id"]: n for n in ns["nodes"]}
    assert by_id["vblockA"]["fnrefs"] == [1], (
        "the same footnote cited in BOTH rendered and literal_gloss is ONE "
        "citation, not a duplicate -- must appear exactly once in fnrefs"
    )
    assert [fn["n"] for fn in ns["footnotes"]].count(1) == 1


def test_verse_fnref_duplicated_within_field_is_fatal(tmp_path):
    """r6 finding 4 companion -- the same footnote cited TWICE inside ONE
    field (rendered alone) is a genuine duplicate_footnote_ref, matching
    assemble's block-branch 'repeat anywhere' invariant. RED against a
    cross-field-only set() that collapses same-field repeats (a bare set()
    over all tokens in one field would silently drop the repeat)."""
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 0,
                        "plain_text": V_PH_A},
            "FN1": {"type": "FN", "seg": None, "order_index": 1, "plain_text": "The definition."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["vblockA"], "word_count": 10}],
        footnotes=[{"n": 1, "anchor_block": "vblockA", "anchor_seg": "seg01", "def_block": "FN1"}],
        verse_store=[{"vid": "vA", "placeholder": V_PH_A, "context": "body",
                       "parent_block": "vblockA", "mount": "block"}],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "vblockA", "order_index": 0, "plain_text": V_PH_A}],
        footnotes=[{"n": 1, "source_text": "The definition."}],
        verses=[{"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"}],
    )
    write_draft(
        root, "seg01",
        blocks={"vblockA": V_PH_A},
        footnotes={"1": "The translated definition."},
        verses={"vA": {"rendered": f"{FN_PH_1} twice in one field {FN_PH_1}",
                       "literal_gloss": "No ref here"}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert (
        payload.get("reason") == "duplicate_footnote_ref"
        or "duplicate" in payload["error"].lower()
    )


def test_unrecognized_sentinel_in_verse_content_is_fatal(tmp_path):
    """codex HIGH (post-hoc round): _scan_verse_content_fnrefs's per-field
    token scan only ever inspected tokens matching FNREF_RE, silently
    ignoring any OTHER ⟦...⟧ sentinel ANY_SENTINEL_RE.findall found -- unlike
    the block-text branch's terminal else-raise (:594), a stray/unregistered
    sentinel (here an unrelated verse placeholder) inside a verse's own
    content used to leak through completely unvalidated (assemble exited 0,
    and the raw sentinel survived byte-for-byte into the rendered book). A
    verse's own translated content may only ever cite ⟦FNREF_n⟧ -- there is
    no such thing as a verse embedding another verse (verse.store[].context
    is only ever "body"/"footnote"/"frontback", never "verse") -- any other
    sentinel here is a data defect and must fail closed, exactly like the
    block branch. REGRESSION-class (git-stash the fix -> confirm RED)."""
    stray = "⟦VERSE_vZ_00000000⟧"
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 0,
                        "plain_text": V_PH_A},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["vblockA"], "word_count": 10}],
        verse_store=[{"vid": "vA", "placeholder": V_PH_A, "context": "body",
                       "parent_block": "vblockA", "mount": "block"}],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "vblockA", "order_index": 0, "plain_text": V_PH_A}],
        verses=[{"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"}],
    )
    write_draft(
        root, "seg01",
        blocks={"vblockA": V_PH_A},
        # vA's own content carries a stray sentinel that is neither a known
        # ⟦FNREF_N⟧ nor anything this segment's block-text scan ever sees.
        verses={"vA": {"rendered": f"A line with {stray} embedded.",
                       "literal_gloss": "A gloss line, no sentinel here."}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert "unrecognized" in payload["error"].lower() or "sentinel" in payload["error"].lower()
    assert not (root / "out" / ".assembled" / "nodestream.json").is_file(), (
        "a fatal fail-closed error must not leave a partial nodestream.json artifact"
    )


def test_unbalanced_bracket_in_verse_content_is_fatal(tmp_path):
    """codex HIGH companion: _scan_verse_content_fnrefs dropped the block
    branch's bracket-balance guard (:477-485) entirely -- an unclosed
    ⟦FNREF_9 (no closing ⟧) inside a verse's own content used to be silently
    swallowed (ANY_SENTINEL_RE requires a closing bracket to match at all,
    so it simply found nothing there and moved on). REGRESSION-class
    (git-stash the fix -> confirm RED)."""
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 0,
                        "plain_text": V_PH_A},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["vblockA"], "word_count": 10}],
        verse_store=[{"vid": "vA", "placeholder": V_PH_A, "context": "body",
                       "parent_block": "vblockA", "mount": "block"}],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "vblockA", "order_index": 0, "plain_text": V_PH_A}],
        verses=[{"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"}],
    )
    write_draft(
        root, "seg01",
        blocks={"vblockA": V_PH_A},
        # An unclosed sentinel bracket -- no matching ⟧ anywhere in the field.
        verses={"vA": {"rendered": "A line with ⟦FNREF_9 unclosed.",
                       "literal_gloss": "A gloss line, no sentinel here."}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert "malformed" in payload["error"].lower() or "bracket" in payload["error"].lower()
    assert not (root / "out" / ".assembled" / "nodestream.json").is_file(), (
        "a fatal fail-closed error must not leave a partial nodestream.json artifact"
    )


def test_verse_parented_to_a_footnote_def_block_but_never_actually_placed_is_still_orphan(tmp_path):
    """Boundary companion #1 to the strip test above (per the lead's
    request): the CURRENT fix (see assemble.py ~629,
    `if vid_to_parent.get(vid) in fn_def_block_ids: continue`) exempts a
    verse from the orphan check purely because its REGISTERED parent_block
    is *some* footnote-def block -- unconditionally, without ever checking
    that the placeholder actually appears anywhere in that footnote's own
    text. Here the footnote (FN1) genuinely never mentions the placeholder
    at all -- draft.verses['vGhost'] is defined with real content, segpack
    claims parent_block=FN1, but the placeholder is referenced NOWHERE
    (not in any body block, not in FN1's own def text either). This MUST
    still be a fatal orphan_verse -- if it isn't, the fix has effectively
    neutered the orphan check for every footnote-parented verse."""
    v_ph_ghost = "⟦VERSE_vGhost_00000002⟧"
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                   "plain_text": f"Body text {FN_PH_1} here.", "fnrefs": [1]},
            "FN1": {"type": "FN", "seg": None, "order_index": 1,
                    "plain_text": "A plain definition with no verse citation at all."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
        footnotes=[{"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"}],
        verse_store=[{"vid": "vGhost", "placeholder": v_ph_ghost, "context": "footnote",
                       "parent_block": "FN1", "mount": "embedded"}],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "p1", "order_index": 0, "plain_text": f"Body text {FN_PH_1} here."}],
        footnotes=[{"n": 1, "source_text": "A plain definition with no verse citation at all."}],
        verses=[{"vid": "vGhost", "placeholder": v_ph_ghost, "parent_block": "FN1"}],
    )
    write_draft(
        root, "seg01",
        blocks={"p1": f"Translated body {FN_PH_1} here."},
        # The footnote's OWN text never mentions v_ph_ghost anywhere.
        footnotes={"1": "A translated plain definition with no verse citation at all."},
        # But draft.verses still defines real content for it.
        verses={"vGhost": {"rendered": "Some rendered verse", "literal_gloss": "Some gloss"}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, (
        f"a verse parented to a footnote-def block whose placeholder is "
        f"referenced NOWHERE (not in any body block, not in the footnote's "
        f"own text either) must still be a fatal orphan_verse -- the "
        f"footnote-parent exemption must not be unconditional:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert payload.get("reason") == "orphan_verse" or "orphan" in payload["error"].lower()
    assert "vGhost" in payload["error"]


def test_verse_placeholder_duplicated_across_a_body_block_and_a_footnote_def_text_is_fatal(tmp_path):
    """Boundary companion #2 to the strip test above (per the lead's
    request): the SAME verse placeholder string appearing BOTH in a body
    block's own text (properly claimed, parent_block=p1) AND, verbatim,
    inside a footnote's own def text (a stray duplicate) must still be
    caught as duplicate_verse_placeholder -- proving footnote-embedded
    occurrences genuinely participate in the SAME book-wide duplicate
    detection as body-block occurrences, not a blind, unchecked strip."""
    v_ph_dup = "⟦VERSE_vDup_00000003⟧"
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                   "plain_text": f"Body {FN_PH_1} with embedded {v_ph_dup} verse.",
                   "fnrefs": [1]},
            "FN1": {"type": "FN", "seg": None, "order_index": 1,
                    "plain_text": f"A definition with a duplicated {v_ph_dup} citation."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
        footnotes=[{"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"}],
        # vDup is registered ONLY against p1 -- FN1's own occurrence of the
        # identical placeholder STRING below is a stray duplicate, not a
        # second, separately-registered verse.
        verse_store=[{"vid": "vDup", "placeholder": v_ph_dup, "context": "body",
                       "parent_block": "p1", "mount": "embedded"}],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "p1", "order_index": 0,
                 "plain_text": f"Body {FN_PH_1} with embedded {v_ph_dup} verse."}],
        footnotes=[{"n": 1, "source_text": f"A definition with a duplicated {v_ph_dup} citation."}],
        verses=[{"vid": "vDup", "placeholder": v_ph_dup, "parent_block": "p1"}],
    )
    write_draft(
        root, "seg01",
        blocks={"p1": f"Translated body {FN_PH_1} with embedded {v_ph_dup} verse."},
        footnotes={"1": f"Translated def with a duplicated {v_ph_dup} citation."},
        verses={"vDup": {"rendered": "Some rendered verse", "literal_gloss": "Some gloss"}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, (
        f"the same verse placeholder appearing in BOTH a body block and a "
        f"footnote's own def text must be caught as a book-wide duplicate, "
        f"not silently stripped away unchecked in the footnote:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert (
        payload.get("reason") == "duplicate_verse_placeholder"
        or "duplicate" in payload["error"].lower()
    )


# ===========================================================================
# 7. frontback dispositions (contract §4.5).
# ===========================================================================


def _book_with_one_frontback(root, decision):
    write_manifest(
        root,
        blocks={
            # The frontback id ITSELF must be a manifest.blocks{} key (with
            # its own order_index) -- assemble.py's regenerate path looks
            # this up directly (manifest_blocks.get(fb_id)) to position the
            # synthesized placeholder node; without it, regenerate silently
            # SKIPS (see assemble.py's own "cannot place it safely" WARN).
            "FRONTBACK:cover": {"type": "FRONTBACK", "seg": None, "order_index": 0,
                                "plain_text": "Cover blurb.", "decision": decision,
                                "origin": "front-matter", "reason": "fixture"},
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 1, "plain_text": "Body text."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
        frontback=[{"id": "FRONTBACK:cover", "decision": decision}],
    )
    write_segpack(root, "seg01", blocks=[{"id": "p1", "order_index": 0, "plain_text": "Body text."}])
    write_draft(root, "seg01", blocks={"p1": "Translated body text."})
    write_ledger(root, {"seg01": {"status": "converged"}})


def test_frontback_translate_gets_a_normal_segment_entry(tmp_path):
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "fb1": {"type": "FRONTBACK", "seg": "FRONTBACK:cover", "order_index": 0,
                    "plain_text": "Cover blurb.", "decision": "translate"},
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 1, "plain_text": "Body text."},
        },
        segments=[
            {"seg": "FRONTBACK:cover", "kind": "frontback", "title_text": "Cover",
             "block_ids": ["fb1"], "word_count": 5},
            {"seg": "seg01", "kind": "body", "title_text": "Ch1",
             "block_ids": ["p1"], "word_count": 10},
        ],
        frontback=[{"id": "FRONTBACK:cover", "decision": "translate"}],
    )
    write_segpack(root, "FRONTBACK:cover", blocks=[{"id": "fb1", "order_index": 0, "plain_text": "Cover blurb."}])
    write_draft(root, "FRONTBACK:cover", blocks={"fb1": "Translated cover blurb."})
    write_segpack(root, "seg01", blocks=[{"id": "p1", "order_index": 0, "plain_text": "Body text."}])
    write_draft(root, "seg01", blocks={"p1": "Translated body text."})
    write_ledger(root, {"FRONTBACK:cover": {"status": "converged"}, "seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    ns = read_nodestream(root)
    assert "FRONTBACK:cover" in ns["book"]["seg_order"]
    node_ids = {n["id"] for n in ns["nodes"]}
    assert "fb1" in node_ids


def test_frontback_omit_is_dropped_entirely(tmp_path):
    root = make_root(tmp_path)
    _book_with_one_frontback(root, "omit")

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    ns = read_nodestream(root)
    assert "FRONTBACK:cover" not in ns["book"]["seg_order"]
    assert all(n["seg"] != "FRONTBACK:cover" for n in ns["nodes"])


def test_frontback_regenerate_emits_a_placeholder_node_and_a_warning(tmp_path):
    root = make_root(tmp_path)
    _book_with_one_frontback(root, "regenerate")

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    ns = read_nodestream(root)
    placeholder_nodes = [n for n in ns["nodes"] if n.get("seg") == "FRONTBACK:cover"]
    assert len(placeholder_nodes) >= 1, (
        "a regenerate-decision frontback unit must synthesize a documented "
        "placeholder node (contract §4.5), not vanish silently"
    )
    assert placeholder_nodes[0]["text"].strip(), "the placeholder node must carry some non-empty text"
    assert "regenerate" in result.stderr.lower() and "cover" in result.stderr, (
        "a regenerate disposition must be surfaced as a warning naming the frontback id"
    )
    # #210 R1: assemble.py has exactly two all_nodes.append() call sites --
    # the ordinary per-block loop and this frontback-regenerate placeholder
    # -- and this fixture's assembled nodestream contains a node from EACH
    # ("p1" from the ordinary loop, the placeholder from this one), so a
    # loop over every node here genuinely exercises both sites, not just
    # the one this test's name calls out. Assert PRESENCE, not just a
    # compatible value -- a future refactor that drops the key on some path
    # must fail this even if it happens to leave every OTHER field intact.
    for node in ns["nodes"]:
        assert "level" in node, f"node {node['id']!r} missing 'level'"
    assert placeholder_nodes[0]["level"] is None, (
        "the placeholder's kind is hardcoded \"prose\" -- level is a "
        "heading-only concept"
    )


# ===========================================================================
# 8. Ledger convergence + draft-sha1 gate (contract §3).
# ===========================================================================


def test_non_converged_segment_is_fatal_project_incomplete(tmp_path):
    """Whole-project completeness gate (see this file's docstring,
    interpretation #1): a partially-converged project must NOT quietly
    assemble only its converged part -- assemble.py refuses the whole run
    (exit 2, reason "project_incomplete") rather than shipping a book that
    silently omits seg02. Baselining such a partial book would otherwise
    pass render+diff forever and never surface the missing segment."""
    root = make_root(tmp_path)
    build_clean_two_segment_book(root)
    # Downgrade seg02 to non_converged after the clean build above.
    write_ledger(root, {
        "seg01": {"status": "converged"},
        "seg02": {"status": "non_converged", "reason": "still in fix rounds"},
    })

    result = run_assemble(root)
    assert result.returncode == 2, (
        f"a partially-converged project must be refused fail-closed by the "
        f"whole-project completeness gate (exit 2), never assembled as a "
        f"partial book (see this file's docstring, interpretation #1):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert payload.get("reason") == "project_incomplete"
    assert "seg02" in payload["error"], "the not-yet-converged segment should be named"
    # The fixture (make_root/build_clean_two_segment_book) never pre-creates
    # out/.assembled/ -- and the completeness gate refuses BEFORE assemble.py's
    # own ASSEMBLED_DIR.mkdir -- so a refused incomplete project must leave NO
    # partial artifacts at all, not even the directory.
    assembled_dir = root / "out" / ".assembled"
    assert not (assembled_dir / "nodestream.json").is_file(), (
        "a refused (incomplete) project must not leave a partial nodestream.json artifact"
    )
    assert not (assembled_dir / "anchor_map.json").is_file(), (
        "a refused (incomplete) project must not leave a partial anchor_map.json artifact"
    )
    assert not assembled_dir.exists(), (
        "a refused (incomplete) project must not even create out/.assembled/ "
        "(the completeness gate runs before assemble.py's own mkdir)"
    )


def test_malformed_or_empty_manifest_segments_is_fatal(tmp_path):
    """Codex hardening (closes the fail-open residue): a converged ledger
    sitting alongside a manifest whose `segments` inventory is empty, absent,
    or malformed must be refused as `malformed_manifest` (exit 1) -- never
    coerced into an empty required set that fails open into an empty
    "successful" book. Covers both branches of the entry guard: a non-object
    entry AND an object with a non-string `seg`."""

    def _rewrite_segments(root, new_segments):
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        manifest["segments"] = new_segments
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )

    # Case 1 -- empty segments array (ledger still has converged segments).
    root_empty = make_root(tmp_path / "empty")
    build_clean_two_segment_book(root_empty)
    _rewrite_segments(root_empty, [])
    result = run_assemble(root_empty)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert payload.get("reason") == "malformed_manifest"
    assert not (root_empty / "out" / ".assembled" / "nodestream.json").is_file()
    assert not (root_empty / "out" / ".assembled" / "anchor_map.json").is_file()

    # Case 2 -- a malformed object entry (seg is null, not a string).
    root_null = make_root(tmp_path / "nullseg")
    build_clean_two_segment_book(root_null)
    _rewrite_segments(root_null, [{"seg": None}])
    result_null = run_assemble(root_null)
    assert result_null.returncode == 1, f"stdout:\n{result_null.stdout}\nstderr:\n{result_null.stderr}"
    payload_null = parse_one_json_line(result_null)
    assert payload_null["success"] is False
    assert payload_null.get("reason") == "malformed_manifest"

    # Case 3 -- a non-object entry (a bare string instead of a {seg:...} obj).
    root_bare = make_root(tmp_path / "barestr")
    build_clean_two_segment_book(root_bare)
    _rewrite_segments(root_bare, ["seg01"])
    result_bare = run_assemble(root_bare)
    assert result_bare.returncode == 1, f"stdout:\n{result_bare.stdout}\nstderr:\n{result_bare.stderr}"
    payload_bare = parse_one_json_line(result_bare)
    assert payload_bare["success"] is False
    assert payload_bare.get("reason") == "malformed_manifest"


def test_draft_sha1_mismatch_against_reviewed_draft_sha1_is_fatal(tmp_path):
    """A hand-edit the reviewer never saw: the ledger's own recorded
    reviewed_draft_sha1 does not match the CURRENT on-disk draft bytes.
    Per this file's docstring interpretation #2, this is treated as fatal
    for the whole run (mirrors final_audit.py's own hard check 2)."""
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={"p1": {"type": "PARA", "seg": "seg01", "order_index": 0, "plain_text": "Body."}},
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
    )
    write_segpack(root, "seg01", blocks=[{"id": "p1", "order_index": 0, "plain_text": "Body."}])
    draft_bytes = write_draft(root, "seg01", blocks={"p1": "Translated body."})
    wrong_sha1 = hashlib.sha1(draft_bytes + b"tampered-after-review").hexdigest()
    write_ledger(root, {"seg01": {"status": "converged", "reviewed_draft_sha1_override": wrong_sha1}})

    result = run_assemble(root)
    assert result.returncode == 1, (
        f"a draft-sha1/reviewed_draft_sha1 mismatch must fail the whole "
        f"run, never ship silently:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert "seg01" in payload["error"]


def test_reusable_survives_non_canonical_draft_bytes(tmp_path):
    """Companion to test_draft_sha1_mismatch_against_reviewed_draft_sha1_is_fatal
    above: the OPPOSITE case must NOT false-positive. A converged segment's
    on-disk draft is deliberately NON-canonical (keys in human-authored,
    non-alphabetical order, pretty-printed with indentation, and a
    'dispatch_token' metadata field present) -- exactly what a real draft
    looks like on disk, never the compact sorted-key form
    draft_content_sha1() re-serializes to. assemble.py must still assemble
    it: its own freshly recomputed draft-content-sha1 must equal the
    ledger's reviewed_draft_sha1 (itself recorded via the very same
    canonical draft_content_sha1() algorithm ledger_update.py uses in
    production). A regression back to a raw-bytes hash in assemble.py would
    misclassify this as a stale-review mismatch and refuse to assemble the
    whole run, even though nothing about the draft actually changed since
    review.
    """
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={"p1": {"type": "PARA", "seg": "seg01", "order_index": 0, "plain_text": "Body."}},
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
    )
    write_segpack(root, "seg01", blocks=[{"id": "p1", "order_index": 0, "plain_text": "Body."}])

    draft_doc = {
        "notes": [],
        "dispatch_token": "some-run-token:seg01",
        "blocks": {"p1": "Translated body, never touched after review."},
        "seg": "seg01",
        "footnotes": {},
        "verses": {},
        "names": [],
    }
    (root / "segments" / "seg01.draft.json").write_text(
        json.dumps(draft_doc, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # The hash a real reviewer/ledger_update.py would compute: canonical
    # content hash, dispatch_token excluded -- deliberately NOT the raw
    # bytes of the pretty-printed file just written above.
    draft_sha1 = draft_content_sha1_of(draft_doc)
    write_ledger(root, {"seg01": {"status": "converged", "reviewed_draft_sha1_override": draft_sha1}})

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"a non-canonical but otherwise unchanged draft must assemble "
        f"cleanly, never trip a false stale-review mismatch:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["success"] is True

    ns = read_nodestream(root)
    node_ids = [n["id"] for n in ns["nodes"]]
    assert node_ids == ["p1"]


# ===========================================================================
# 9. Monotonic order_index across segments -- surfaced somehow (contract
#    §4.1's own "warn/fail" is genuinely undecided; see docstring #3).
# ===========================================================================


def test_non_monotonic_segment_order_is_surfaced(tmp_path):
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            # seg02 (2nd in segments[] array order) has a LOWER min
            # order_index than seg01 (1st) -- a monotonicity violation.
            "p_a": {"type": "PARA", "seg": "seg01", "order_index": 5, "plain_text": "A."},
            "p_b": {"type": "PARA", "seg": "seg02", "order_index": 0, "plain_text": "B."},
        },
        segments=[
            {"seg": "seg01", "kind": "body", "title_text": "Ch1", "block_ids": ["p_a"], "word_count": 5},
            {"seg": "seg02", "kind": "body", "title_text": "Ch2", "block_ids": ["p_b"], "word_count": 5},
        ],
    )
    write_segpack(root, "seg01", blocks=[{"id": "p_a", "order_index": 0, "plain_text": "A."}])
    write_draft(root, "seg01", blocks={"p_a": "A translated."})
    write_segpack(root, "seg02", blocks=[{"id": "p_b", "order_index": 0, "plain_text": "B."}])
    write_draft(root, "seg02", blocks={"p_b": "B translated."})
    write_ledger(root, {"seg01": {"status": "converged"}, "seg02": {"status": "converged"}})

    result = run_assemble(root)
    combined_output = result.stdout + result.stderr
    assert "order_index" in combined_output or "order-index" in combined_output.lower(), (
        f"a non-monotonic segment order_index run must name the anomaly "
        f"SOMEWHERE in the process output (exit code is deliberately not "
        f"asserted here -- contract §4.1 leaves warn-vs-fail undecided):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ===========================================================================
# 10. Regression tests for codex review-round-1 fixes (FIXSPEC_lt_review1.md,
#     "B / lt-schema" section) -- written to the spec's exact reason strings
#     so they align with the fix in parallel; some may be red until the fix
#     lands (see the run report to the lead).
# ===========================================================================


def test_orphan_footnote_def_is_fatal(tmp_path):
    """FIXSPEC B1: a footnote defined in draft.footnotes (with a real,
    structurally-registered manifest.footnotes[] entry) but never actually
    referenced by any ⟦FNREF_N⟧ sentinel anywhere in this segment's block
    text is an orphan -- a fatal bijection violation, not a silent drop."""
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                   "plain_text": f"Text {FN_PH_1} here.", "fnrefs": [1]},
            "FN1": {"type": "FN", "seg": None, "order_index": 1, "plain_text": "First def."},
            "FN2": {"type": "FN", "seg": None, "order_index": 2, "plain_text": "Orphan def."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
        # n=2 is structurally registered (a real def block, a real anchor
        # entry) but NO block anywhere in seg01 actually contains ⟦FNREF_2⟧.
        footnotes=[
            {"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"},
            {"n": 2, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN2"},
        ],
    )
    write_segpack(root, "seg01", blocks=[
        {"id": "p1", "order_index": 0, "plain_text": f"Text {FN_PH_1} here."},
    ], footnotes=[{"n": 1, "source_text": "First def."}, {"n": 2, "source_text": "Orphan def."}])
    write_draft(
        root, "seg01",
        blocks={"p1": f"Translated {FN_PH_1} here."},
        # draft.footnotes has an ORPHAN "2" entry -- defined, never referenced.
        footnotes={"1": "Translated first def.", "2": "Translated orphan def."},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert payload.get("reason") == "orphan_footnote_def" or "orphan" in payload["error"].lower()
    assert "2" in payload["error"]


def test_orphan_verse_is_fatal(tmp_path):
    """FIXSPEC B1: a verse with real segpack registration + draft content,
    whose placeholder never actually appears anywhere in its own claimed
    parent_block's assembled text, is an orphan -- fatal."""
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 0,
                        "plain_text": "This text does not contain the verse placeholder at all."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["vblockA"], "word_count": 10}],
        verse_store=[{"vid": "vA", "placeholder": V_PH_A, "context": "body",
                       "parent_block": "vblockA", "mount": "block"}],
    )
    write_segpack(root, "seg01", blocks=[
        {"id": "vblockA", "order_index": 0, "plain_text": "Source verse text."},
    ], verses=[{"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"}])
    write_draft(
        root, "seg01",
        # The translated text never actually uses the vA placeholder.
        blocks={"vblockA": "This text does not contain the verse placeholder at all."},
        verses={"vA": {"rendered": "Some rendered verse", "literal_gloss": "Some gloss"}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert payload.get("reason") == "orphan_verse" or "orphan" in payload["error"].lower()
    assert "vA" in payload["error"]


def test_duplicate_verse_placeholder_within_one_block_is_fatal(tmp_path):
    """FIXSPEC B2 (verse half): the SAME verse placeholder appearing MORE
    THAN ONCE (here: twice within the same block's own text) is fatal --
    each verse instance is unique."""
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 0,
                        "plain_text": f"{V_PH_A} middle {V_PH_A}"},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["vblockA"], "word_count": 10}],
        verse_store=[{"vid": "vA", "placeholder": V_PH_A, "context": "body",
                       "parent_block": "vblockA", "mount": "block"}],
    )
    write_segpack(root, "seg01", blocks=[
        {"id": "vblockA", "order_index": 0, "plain_text": "Source verse text."},
    ], verses=[{"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"}])
    write_draft(
        root, "seg01",
        blocks={"vblockA": f"{V_PH_A} middle {V_PH_A}"},
        verses={"vA": {"rendered": "Some rendered verse", "literal_gloss": "Some gloss"}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert (
        payload.get("reason") == "duplicate_verse_placeholder"
        or "duplicate" in payload["error"].lower()
    )


def test_repeated_footnote_reference_across_body_blocks_is_fatal_or_deduped(tmp_path):
    """FIXSPEC B2 (footnote half) -- an OPEN design choice left to B: either
    fatal (duplicate_footnote_ref, a "one-anchor-per-footnote" data model --
    favored here since manifest.schema.json's own anchor_block/anchor_seg
    fields are SINGULAR) or allow+dedup (if a footnote may legitimately be
    cited more than once). This test accepts EITHER valid outcome and
    asserts the specific invariant for whichever path was taken, so it does
    not need updating once B's choice lands -- but FLAG to the lead which
    branch actually fired (see the run report)."""
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                   "plain_text": f"First mention {FN_PH_1}.", "fnrefs": [1]},
            "p2": {"type": "PARA", "seg": "seg01", "order_index": 1,
                   "plain_text": f"Second mention {FN_PH_1}.", "fnrefs": [1]},
            "FN1": {"type": "FN", "seg": None, "order_index": 2, "plain_text": "The definition."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1", "p2"], "word_count": 10}],
        footnotes=[{"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"}],
    )
    write_segpack(
        root, "seg01",
        blocks=[
            {"id": "p1", "order_index": 0, "plain_text": f"First mention {FN_PH_1}."},
            {"id": "p2", "order_index": 1, "plain_text": f"Second mention {FN_PH_1}."},
        ],
        footnotes=[{"n": 1, "source_text": "The definition."}],
    )
    write_draft(
        root, "seg01",
        blocks={"p1": f"First mention {FN_PH_1}.", "p2": f"Second mention {FN_PH_1}."},
        footnotes={"1": "The translated definition."},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    if result.returncode == 1:
        payload = parse_one_json_line(result)
        assert payload["success"] is False
        assert (
            payload.get("reason") == "duplicate_footnote_ref"
            or "duplicate" in payload["error"].lower()
        )
    else:
        assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ns = read_nodestream(root)
        assert [fn["n"] for fn in ns["footnotes"]].count(1) == 1, (
            "the allow+dedup reading must still emit footnote 1's def exactly once"
        )


def test_footnote_def_block_inside_a_body_segments_block_ids_is_fatal(tmp_path):
    """FIXSPEC B3: a malformed manifest listing a footnote-DEFINITION block
    id (a real manifest.footnotes[].def_block) inside a body segment's own
    block_ids must be refused fail-closed -- never silently classified
    'prose' and emitted inline."""
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                   "plain_text": f"Text {FN_PH_1} here.", "fnrefs": [1]},
            "FN1": {"type": "FN", "seg": "seg01", "order_index": 1, "plain_text": "The definition."},
        },
        # MALFORMED: FN1 (a real footnote-def block) is listed in block_ids.
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1", "FN1"], "word_count": 10}],
        footnotes=[{"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"}],
    )
    write_segpack(root, "seg01", blocks=[
        {"id": "p1", "order_index": 0, "plain_text": f"Text {FN_PH_1} here."},
        {"id": "FN1", "order_index": 1, "plain_text": "The definition."},
    ], footnotes=[{"n": 1, "source_text": "The definition."}])
    write_draft(
        root, "seg01",
        blocks={"p1": f"Translated {FN_PH_1} here.", "FN1": "Translated definition."},
        footnotes={"1": "Translated definition."},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert payload.get("reason") == "footnote_def_in_body" or "FN1" in payload["error"]


def test_duplicate_manifest_order_index_is_fatal(tmp_path):
    """FIXSPEC B4: two blocks sharing an order_index is an ambiguous global
    reading axis -- fatal. (Gaps in the sequence stay fine, untested here
    since every other test in this file already relies on that.)"""
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0, "plain_text": "First."},
            "p2": {"type": "PARA", "seg": "seg01", "order_index": 0, "plain_text": "Second."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1", "p2"], "word_count": 10}],
    )
    write_segpack(root, "seg01", blocks=[
        {"id": "p1", "order_index": 0, "plain_text": "First."},
        {"id": "p2", "order_index": 1, "plain_text": "Second."},
    ])
    write_draft(root, "seg01", blocks={"p1": "First translated.", "p2": "Second translated."})
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert payload.get("reason") == "duplicate_order_index" or "order_index" in payload["error"]


def test_profile_precondition_emits_one_json_line_on_missing_ownership_marker(tmp_path):
    """FIXSPEC B5: vd.load_profile() can sys.exit(2) with a stderr-only
    message (no ownership marker at all -- Step 0a never ran for this
    fixture). assemble.py must catch that SystemExit and re-emit ONE JSON
    line, {"success": false, "reason": "profile_precondition", ...}, still
    at exit code 2 (a defined precondition, not a fatal defect)."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC, VALIDATE_DRAFT_SRC):
        shutil.copy2(src, scripts_dir / src.name)
    # Deliberately NO profile.yml, NO .literary-translator-root.json marker.

    result = run_assemble(root)
    assert result.returncode == 2, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert payload.get("reason") == "profile_precondition"


def test_atomic_artifact_writes_stay_valid_when_adapter_dispatch_fails(tmp_path):
    """FIXSPEC B6: a failed adapter dispatch must never leave a TRUNCATED/
    corrupt nodestream.json or anchor_map.json on disk. Per the spec,
    writing the artifacts BEFORE dispatch is fine -- so whatever DOES exist
    after a failure must be complete, valid JSON matching the exact
    contractual shape, never a half-written file. Isolates "adapter
    dispatch failed" from any real render_obsidian.py behavior via a
    deliberately-raising custom renderer."""
    root = make_root(tmp_path, output_target="custom", custom_renderer_path="boom_renderer.py")
    custom_dir = root / "scripts" / "custom_renderers"
    custom_dir.mkdir(parents=True)
    (custom_dir / "boom_renderer.py").write_text(
        "def render(nodestream, canon, profile, out_dir):\n"
        "    raise RuntimeError('boom - deliberate adapter failure for atomicity test')\n",
        encoding="utf-8",
    )
    write_manifest(
        root,
        blocks={"p1": {"type": "PARA", "seg": "seg01", "order_index": 0, "plain_text": "Body."}},
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
    )
    write_segpack(root, "seg01", blocks=[{"id": "p1", "order_index": 0, "plain_text": "Body."}])
    write_draft(root, "seg01", blocks={"p1": "Translated body."})
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, (
        f"a deliberately-raising custom adapter must fail the whole run:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert "boom" in payload["error"] or "adapter" in payload["error"].lower()

    # Whatever nodestream.json/anchor_map.json DOES exist must be complete,
    # valid JSON matching the exact contractual shape -- never truncated.
    ns_path = root / "out" / ".assembled" / "nodestream.json"
    am_path = root / "out" / ".assembled" / "anchor_map.json"
    if ns_path.is_file():
        ns = json.loads(ns_path.read_text(encoding="utf-8"))
        assert set(ns.keys()) == {"book", "nodes", "footnotes", "meta"}
        assert len(ns["nodes"]) == 1 and ns["nodes"][0]["id"] == "p1"
    if am_path.is_file():
        am = json.loads(am_path.read_text(encoding="utf-8"))
        assert set(am.keys()) == {"blocks", "footnotes", "verses"}


def test_final_audit_shell_out_is_removed(tmp_path):
    """FIXSPEC B7: assemble.py must no longer shell out to final_audit.py at
    all (the whole-project completeness check was advisory-only, gated
    nothing, and could burn up to 300s) -- proportionality guardrail per
    contract §15. Behavioral lock (the PRIMARY assertion): a POISON-PILL
    final_audit.py stub writes a marker file the instant it is ever
    invoked; after a normal assemble.py run, that marker must NOT exist.

    The static check is deliberately narrow -- NOT a blanket "final_audit"
    substring absence (assemble.py's own source legitimately keeps several
    comparative doc-comments mirroring final_audit.py's OWN unrelated
    conventions, e.g. "mirroring final_audit.py's own X pattern", which are
    fine to keep) -- only the actual machinery a shell-out would need
    (a `subprocess` import, or a `check_project_complete_advisory`-shaped
    function) must be gone."""
    root = make_root(tmp_path)
    build_clean_two_segment_book(root)
    poison_pill = (
        "from pathlib import Path\n"
        "marker = Path(__file__).resolve().parents[1] / 'runs' / 'final_audit_was_invoked.marker'\n"
        "marker.write_text('invoked\\n')\n"
        "print('{\"project_complete\": false}')\n"
    )
    (root / "scripts" / "final_audit.py").write_text(poison_pill, encoding="utf-8")

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    marker = root / "runs" / "final_audit_was_invoked.marker"
    assert not marker.exists(), (
        "assemble.py must no longer shell out to final_audit.py at all -- "
        "the poison-pill stub's marker file must never be created"
    )

    assemble_source = ASSEMBLE_SRC.read_text(encoding="utf-8")
    assert "import subprocess" not in assemble_source, (
        "assemble.py must no longer import subprocess at all -- its only "
        "use was the removed final_audit.py shell-out"
    )
    assert "check_project_complete_advisory" not in assemble_source, (
        "the advisory-only whole-project completeness shell-out function "
        "itself must be deleted, not merely left unused"
    )


# ===========================================================================
# 11. Round-2 regression tests (FIXSPEC_lt_review2.md, "B / lt-schema").
# ===========================================================================


def test_dependency_precondition_emits_one_json_line_when_validate_draft_exits_at_import(tmp_path):
    """FIXSPEC B2: validate_draft.py's own module-level dependency preflight
    (its PyYAML import guard) can sys.exit(2) DURING the `import
    validate_draft` statement itself -- before assemble.py's own JSON-
    envelope machinery ever runs. Hermetic repro: a POISONED validate_draft.py
    stub that sys.exits(2) at import time (mirroring the real one's own
    failure mode) -- never touches the real environment's PyYAML install."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC):
        shutil.copy2(src, scripts_dir / src.name)
    (scripts_dir / "validate_draft.py").write_text(
        "import sys\n"
        "print('ERROR: poisoned validate_draft.py dependency preflight', file=sys.stderr)\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )
    # Deliberately no profile.yml/marker -- the import fails before either
    # would ever be consulted.

    result = run_assemble(root)
    assert result.returncode == 2, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert payload.get("reason") == "dependency_precondition"


def test_duplicate_manifest_verse_store_vid_is_fatal_documents_intentional_check(tmp_path):
    """FIXSPEC B1/DISMISSED: codex misread this as a false-positive
    ("bare-vid collision") -- it isn't. manifest.verse.store's own `vid` is
    GLOBALLY unique book-wide (manifest.schema.json's own "this verse's
    unique key" description), a stronger guarantee than segpack's own
    per-segment-local vid (which is why the cross-block DUPLICATE-
    PLACEHOLDER check elsewhere keys on the placeholder STRING, not the
    bare vid -- a different, weaker space). This test documents that a
    genuinely duplicate manifest.verse.store vid IS and MUST remain fatal."""
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 0, "plain_text": V_PH_A},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["vblockA"], "word_count": 10}],
        # Two DIFFERENT verse.store entries sharing the SAME vid -- a
        # genuine manifest-level defect, never a legitimate scenario.
        verse_store=[
            {"vid": "vA", "placeholder": V_PH_A, "context": "body",
             "parent_block": "vblockA", "mount": "block"},
            {"vid": "vA", "placeholder": "⟦VERSE_vA_deadbeef2⟧", "context": "body",
             "parent_block": "vblockA", "mount": "block"},
        ],
    )
    write_segpack(root, "seg01", blocks=[
        {"id": "vblockA", "order_index": 0, "plain_text": V_PH_A},
    ], verses=[{"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"}])
    write_draft(
        root, "seg01",
        blocks={"vblockA": V_PH_A},
        verses={"vA": {"rendered": "Some rendered verse", "literal_gloss": "Some gloss"}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, (
        f"a genuinely duplicate manifest.verse.store vid must be fatal -- "
        f"this is an intentional, correct check, not a false-positive:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert "duplicate" in payload["error"].lower() and "vA" in payload["error"]


# ===========================================================================
# 12. Round-5 regression tests (FIXSPEC_lt_review5.md, "B / lt-schema"):
#     `.assembled/` is a preserved dotfile (render_obsidian's own
#     clean-render never recurses into it), so a planted
#     `out/.assembled -> /external/dir` symlink could survive indefinitely
#     -- assemble.py now refuses outright rather than mkdir/write through
#     it, and its own artifact write uses the same mkstemp+os.replace
#     no-follow pattern as render_obsidian's marker/baseline writes.
# ===========================================================================


def test_assembled_dir_symlink_is_refused_and_external_dir_untouched(tmp_path):
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={"p1": {"type": "PARA", "seg": "seg01", "order_index": 0, "plain_text": "Body."}},
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
    )
    write_segpack(root, "seg01", blocks=[{"id": "p1", "order_index": 0, "plain_text": "Body."}])
    write_draft(root, "seg01", blocks={"p1": "Translated body."})
    write_ledger(root, {"seg01": {"status": "converged"}})

    external_dir = tmp_path / "external_assembled_dir"
    external_dir.mkdir()
    external_file = external_dir / "precious.json"
    external_file.write_text('{"precious": true}', encoding="utf-8")

    assembled_symlink = root / "out" / ".assembled"
    assembled_symlink.parent.mkdir(parents=True, exist_ok=True)
    assembled_symlink.symlink_to(external_dir, target_is_directory=True)

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert payload.get("reason") == "assembled_dir_is_symlink"

    assert external_file.is_file() and external_file.read_text(encoding="utf-8") == '{"precious": true}', (
        "a refused assemble must leave the symlink's external target completely untouched"
    )
    assert list(external_dir.iterdir()) == [external_file], (
        "nothing must be written into the external dir the symlink points at"
    )


def test_assembled_artifacts_are_real_files_with_no_stray_tmp_after_normal_assemble(tmp_path):
    """Positive companion: after a normal, successful assemble, both
    artifacts are real regular files (never symlinks) containing valid
    JSON, and NO stray temp file survives -- neither the new mkstemp-based
    "lt-assembled-tmp-*" prefix nor the old predictable
    "<name>.tmp.<pid>" pattern it replaced."""
    root = make_root(tmp_path)
    build_clean_two_segment_book(root)

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    assembled_dir = root / "out" / ".assembled"
    nodestream_path = assembled_dir / "nodestream.json"
    anchor_map_path = assembled_dir / "anchor_map.json"

    for p in (nodestream_path, anchor_map_path):
        assert p.is_file() and not p.is_symlink(), f"{p} must be a real regular file, not a symlink"
        json.loads(p.read_text(encoding="utf-8"))  # must be valid, complete JSON

    leftover_new_style = list(assembled_dir.glob("lt-assembled-tmp-*"))
    assert leftover_new_style == [], f"expected zero stray mkstemp tmp entries, found: {leftover_new_style}"
    leftover_old_style = [p for p in assembled_dir.iterdir() if ".tmp." in p.name]
    assert leftover_old_style == [], (
        f"expected zero old-style *.tmp.<pid> strays (the predictable-name "
        f"scheme this fix replaced), found: {leftover_old_style}"
    )


# ===========================================================================
# 13. Round-6 regression test: the PARENT of `.assembled/` -- `out/` itself
#     (a direct child of the trusted DURABLE_ROOT) -- being a symlink must
#     be refused BEFORE the `.assembled`-itself check even runs, since
#     `mkdir(parents=True)`/mkstemp would otherwise write straight into an
#     external target through a planted `out -> /external` symlink.
# ===========================================================================


def test_parent_out_dir_symlink_is_refused_before_assembled_dir_is_created(tmp_path):
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={"p1": {"type": "PARA", "seg": "seg01", "order_index": 0, "plain_text": "Body."}},
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
    )
    write_segpack(root, "seg01", blocks=[{"id": "p1", "order_index": 0, "plain_text": "Body."}])
    write_draft(root, "seg01", blocks={"p1": "Translated body."})
    write_ledger(root, {"seg01": {"status": "converged"}})

    external_dir = tmp_path / "external_out_dir"
    external_dir.mkdir()
    external_file = external_dir / "precious.txt"
    external_file.write_text("precious external content", encoding="utf-8")

    out_symlink = root / "out"
    out_symlink.symlink_to(external_dir, target_is_directory=True)

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert payload.get("reason") == "out_dir_is_symlink"

    assert external_file.is_file() and external_file.read_text(encoding="utf-8") == "precious external content", (
        "a refused assemble must leave the symlink's external target completely untouched"
    )
    assert not (external_dir / ".assembled").exists(), (
        "no .assembled/ may ever be created inside the external target the "
        "planted out/ symlink points at"
    )
    assert sorted(p.name for p in external_dir.iterdir()) == ["precious.txt"], (
        "nothing new may be written into the external dir"
    )


# ===========================================================================
# 14. PR #76 bot blocker #4 (end-to-end): output.destination reached through
#     a symlinked PARENT must be refused by output_resolve.resolve_out_dir's
#     no-follow guard -- assemble maps its OutputResolveError to AssembleError
#     (exit 1) and NOTHING is written into the external symlink target. This
#     is the escape the bot reproduced when resolve_out_dir still used
#     .resolve() (which follows every symlink component).
# ===========================================================================


def test_output_destination_via_symlinked_parent_is_refused_no_escape(tmp_path):
    import yaml

    root = make_root(tmp_path)
    build_clean_two_segment_book(root)

    external = tmp_path / "external_escape_target"
    external.mkdir()
    linkparent = root / "linkparent"
    linkparent.symlink_to(external, target_is_directory=True)

    # Repoint output.destination THROUGH the symlinked parent (absolute, the
    # same shape the default fixture profile already uses).
    profile = yaml.safe_load((root / "profile.yml").read_text(encoding="utf-8"))
    profile["output"]["destination"] = str(linkparent / "vault")
    (root / "profile.yml").write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")

    result = run_assemble(root)
    assert result.returncode == 1, (
        f"a destination reached through a symlinked parent must be refused "
        f"(AssembleError, exit 1), never followed into the external target:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert "symlink" in payload["error"].lower()

    # NOTHING escaped into the external target the symlink points at -- the
    # guard refuses BEFORE out_dir.mkdir and BEFORE the adapter ever renders.
    assert not (external / ".literary-translator-vault.json").exists(), (
        "the vault ownership marker must never be written into the external symlink target"
    )
    assert list(external.glob("**/*.md")) == [], (
        f"no rendered note may be written into the external symlink target; "
        f"found: {list(external.glob('**/*.md'))}"
    )
    assert list(external.iterdir()) == [], (
        f"the external symlink target must be left completely untouched; "
        f"found: {list(external.iterdir())}"
    )


# ===========================================================================
# 15. codex-rescue adversarial review: a built-in adapter module (e.g.
#     render_obsidian.py) can sys.exit() DURING dispatch_adapter()'s own
#     `__import__(adapter)` call -- deep inside main()'s try block, well
#     after profile/manifest/ledger/nodestream have all already succeeded.
#     SystemExit deliberately does not subclass Exception, so it escaped
#     both dispatch_adapter()'s own `except Exception` and main()'s
#     outermost `except Exception` catch-all, crashing the process with NO
#     JSON on stdout -- breaking the documented one-JSON-line contract.
#     Distinct from test_dependency_precondition_emits_one_json_line_when_
#     validate_draft_exits_at_import above, which pokes the SAME failure
#     mode at assemble.py's own TOP-OF-FILE sibling import, long before
#     main() ever starts; this one pokes it at the adapter-dispatch import
#     boundary specifically.
# ===========================================================================


def test_dependency_precondition_emits_one_json_line_when_builtin_adapter_exits_at_import(tmp_path):
    root = make_root(tmp_path)
    build_clean_two_segment_book(root)

    # Poison the built-in adapter module itself (render_obsidian.py) with a
    # stub that sys.exits(2) at import time -- mirroring its own real
    # module-level PyYAML guard's failure mode -- AFTER a fully valid
    # profile/manifest/ledger/nodestream have already been built, so the
    # SystemExit is guaranteed to originate from dispatch_adapter()'s
    # `__import__(adapter)` call, not from assemble.py's own top-of-file
    # imports.
    (root / "scripts" / "render_obsidian.py").write_text(
        "import sys\n"
        "print('ERROR: poisoned render_obsidian.py dependency preflight', file=sys.stderr)\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )

    result = run_assemble(root)
    payload = parse_one_json_line(result)
    assert result.returncode == 2, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert payload["success"] is False
    assert payload.get("reason") == "dependency_precondition"

    # Whatever nodestream.json/anchor_map.json DOES exist (written before
    # dispatch) must still be complete, valid JSON -- never truncated.
    ns_path = root / "out" / ".assembled" / "nodestream.json"
    if ns_path.is_file():
        json.loads(ns_path.read_text(encoding="utf-8"))


def test_dependency_precondition_preserves_string_sys_exit_message_from_builtin_adapter(tmp_path):
    # codex-rescue adversarial review: dispatch_adapter()'s `except
    # SystemExit` handlers classified EVERY SystemExit as
    # `dependency_precondition` with a fixed generic "see stderr" message,
    # discarding whatever `sys.exit("...")` actually packed into
    # `exc.code` -- that string is never auto-printed by Python once this
    # handler catches it, so it was silently lost even when the halting
    # module wrote nothing of its own to stderr. This proves the specific
    # message now survives into the JSON payload's `error` field.
    root = make_root(tmp_path)
    build_clean_two_segment_book(root)

    (root / "scripts" / "render_obsidian.py").write_text(
        "import sys\n"
        "sys.exit('some specific custom reason')\n",
        encoding="utf-8",
    )

    result = run_assemble(root)
    payload = parse_one_json_line(result)
    assert result.returncode == 2, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert payload["success"] is False
    assert payload.get("reason") == "dependency_precondition"
    assert "some specific custom reason" in payload["error"], payload["error"]


def test_custom_renderer_system_exit_gets_adapter_import_precondition_not_dependency_precondition(tmp_path):
    # codex-rescue adversarial review (substitute, round 5): dispatch_adapter()'s
    # custom-renderer branch reused the built-in-adapter branch's
    # `dependency_precondition` reason verbatim -- but a custom renderer is an
    # open, user-authored extension point, so its own module-level sys.exit()
    # is not necessarily about a missing dependency. Reusing that reason (and
    # its "dependency preflight" wording) mischaracterizes the failure for any
    # caller that branches on `reason` alone. This proves the custom-renderer
    # path now gets its own distinct, honest reason.
    root = make_root(tmp_path, output_target="custom", custom_renderer_path="halts_renderer.py")
    custom_dir = root / "scripts" / "custom_renderers"
    custom_dir.mkdir(parents=True)
    (custom_dir / "halts_renderer.py").write_text(
        "import sys\n"
        "sys.exit('missing API_KEY env var for license check')\n",
        encoding="utf-8",
    )
    build_clean_two_segment_book(root)

    result = run_assemble(root)
    payload = parse_one_json_line(result)
    assert result.returncode == 2, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert payload["success"] is False
    assert payload.get("reason") == "adapter_import_precondition"
    assert payload.get("reason") != "dependency_precondition"
    assert "missing API_KEY env var for license check" in payload["error"], payload["error"]


# ===========================================================================
# 16. #118 item 1 (Fix B) -- verse_policy.mode: skip footnote deadlock, and
#     #118 item 2 (Fix C) -- a footnote cited only inside a verse that is
#     itself embedded in another footnote's def text (arbitrary nesting depth).
#     Fix B's orphan_verse extension reuses Fix C's shared def-embedded-verse
#     helper, so the two are exercised together here.
# ===========================================================================

FN_PH_3 = "⟦FNREF_3⟧"
V_PH_OUTER = "⟦VERSE_vOuter_11111111⟧"
V_PH_INNER = "⟦VERSE_vInner_22222222⟧"
V_PH_INNER2 = "⟦VERSE_vInner2_33333333⟧"
V_PH_GHOST = "⟦VERSE_vGhost_44444444⟧"


def _all_rendered_markdown(root: Path) -> str:
    """Concatenated text of every rendered .md the obsidian adapter wrote under
    out/. Used to prove a footnote/verse that is stripped-not-rendered leaves
    NO dangling `[^n]:` definition and NO leaked raw sentinel in the real
    output, not just in the intermediate nodestream."""
    return "".join(p.read_text(encoding="utf-8") for p in (root / "out").rglob("*.md"))


def test_skip_mode_footnote_cited_only_in_voided_verse_content_is_not_orphan(tmp_path):
    """Fix B base exemption: under verse_policy.mode: skip a verse's content is
    voided ({}), so a footnote whose SOLE citation site is that verse's content
    can never be discovered by any sentinel scan -- yet the draft still supplies
    placeholder footnote text (satisfies validate_draft check 4). Pre-fix this
    deadlocks as a fatal orphan_footnote_def; post-fix it is exempted (the
    manifest's mode-independent verse.store.fnrefs proves the footnote is
    verse-cited). The footnote is stripped-not-rendered: no dangling [^1]:."""
    root = make_root(tmp_path, verse_mode="skip")
    write_manifest(
        root,
        blocks={
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 0,
                        "plain_text": V_PH_OUTER},
            "FN1": {"type": "FN", "seg": None, "order_index": 1,
                    "plain_text": "A plain footnote about the poem."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["vblockA"], "word_count": 10}],
        footnotes=[{"n": 1, "anchor_block": "vblockA", "anchor_seg": "seg01", "def_block": "FN1"}],
        # The manifest's own ground truth: verse vOuter's SOURCE cites footnote 1.
        verse_store=[{"vid": "vOuter", "placeholder": V_PH_OUTER, "context": "body",
                       "parent_block": "vblockA", "mount": "block", "fnrefs": [1]}],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "vblockA", "order_index": 0, "plain_text": V_PH_OUTER}],
        footnotes=[{"n": 1, "source_text": "A plain footnote about the poem."}],
        verses=[{"vid": "vOuter", "placeholder": V_PH_OUTER, "parent_block": "vblockA"}],
    )
    write_draft(
        root, "seg01",
        blocks={"vblockA": V_PH_OUTER},
        # draft.footnotes supplies text (check 4); draft.verses is voided (skip).
        footnotes={"1": "Translated footnote about the poem."},
        verses={"vOuter": {}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"a skip-mode footnote cited only inside a mode-voided verse's content "
        f"must be exempted, not fatally orphaned:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    ns = read_nodestream(root)
    assert 1 not in {fn["n"] for fn in ns["footnotes"]}, (
        "the exempted (stripped-not-rendered) footnote must NOT enter the "
        "book-wide footnotes[] table"
    )
    assert all(1 not in n["fnrefs"] for n in ns["nodes"]), (
        "the exempted footnote must never join any node's fnrefs"
    )
    md = _all_rendered_markdown(root)
    assert "[^1]:" not in md, f"no dangling footnote definition may render -- got:\n{md}"


def test_skip_mode_genuine_orphan_footnote_still_fatal(tmp_path):
    """Fix B is scoped, not a blanket skip-mode bypass: a footnote defined in
    the draft but cited by NO verse (and no prose) under skip is still a
    genuine orphan and must still fatally raise orphan_footnote_def."""
    root = make_root(tmp_path, verse_mode="skip")
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                   "plain_text": "Prose with no footnote citation at all."},
            "FN9": {"type": "FN", "seg": None, "order_index": 1, "plain_text": "Orphan def."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
        footnotes=[{"n": 9, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN9"}],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "p1", "order_index": 0, "plain_text": "Prose with no footnote citation at all."}],
        footnotes=[{"n": 9, "source_text": "Orphan def."}],
    )
    write_draft(
        root, "seg01",
        blocks={"p1": "Translated prose with no footnote citation at all."},
        footnotes={"9": "Translated orphan def."},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, (
        f"a genuine orphan footnote (cited by no verse) must still fatal even "
        f"under skip mode:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert payload.get("reason") == "orphan_footnote_def" or "orphan" in payload["error"].lower()
    assert "9" in payload["error"]


def test_skip_mode_exemption_uses_plain_text_fallback_when_fnrefs_stale(tmp_path):
    """Fix B robustness: the exemption condition mirrors the UNION segpack
    itself uses -- verse.store.fnrefs OR a direct ⟦FNREF_n⟧ scan of
    verse.store.plain_text. A stale manifest whose sentinel survives in
    plain_text but is missing from fnrefs[] (the exact case segpack.py WARNs
    about) must still exempt, never re-open the deadlock through a gap in the
    exemption's own condition."""
    root = make_root(tmp_path, verse_mode="skip")
    write_manifest(
        root,
        blocks={
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 0,
                        "plain_text": V_PH_OUTER},
            "FN1": {"type": "FN", "seg": None, "order_index": 1, "plain_text": "A footnote."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["vblockA"], "word_count": 10}],
        footnotes=[{"n": 1, "anchor_block": "vblockA", "anchor_seg": "seg01", "def_block": "FN1"}],
        # STALE: fnrefs[] omits 1, but plain_text still carries the ⟦FNREF_1⟧
        # sentinel -- the union must still recognize the verse cites footnote 1.
        verse_store=[{"vid": "vOuter", "placeholder": V_PH_OUTER, "context": "body",
                       "parent_block": "vblockA", "mount": "block", "fnrefs": [],
                       "plain_text": f"Poem line {FN_PH_1} in source"}],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "vblockA", "order_index": 0, "plain_text": V_PH_OUTER}],
        footnotes=[{"n": 1, "source_text": "A footnote."}],
        verses=[{"vid": "vOuter", "placeholder": V_PH_OUTER, "parent_block": "vblockA"}],
    )
    write_draft(
        root, "seg01",
        blocks={"vblockA": V_PH_OUTER},
        footnotes={"1": "Translated footnote."},
        verses={"vOuter": {}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"the exemption must recognize a footnote cited via the verse's "
        f"plain_text even when fnrefs[] is stale:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_skip_mode_case_g_footnote_def_embeds_verse_succeeds(tmp_path):
    """Fix B orphan_verse extension (the round-3 must-fix), issue's own primary
    topology under skip: vOuter (mount:block) cites fn1 only inside its content,
    and fn1's def text embeds a SECOND verse vInner. Under skip vOuter's content
    is voided, so fn1 is never discovered -> base-exempted; the exemption must
    ALSO mark vInner referenced (via the shared def-scan helper), else the
    orphan_verse loop false-fatals vInner. Two-stage red: orphan_footnote_def
    before the base exemption, orphan_verse before the extension."""
    root = make_root(tmp_path, verse_mode="skip")
    write_manifest(
        root,
        blocks={
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 0,
                        "plain_text": V_PH_OUTER},
            "FN1": {"type": "FN", "seg": None, "order_index": 1,
                    "plain_text": f"A note quoting another poem: {V_PH_INNER}"},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["vblockA"], "word_count": 10}],
        footnotes=[{"n": 1, "anchor_block": "vblockA", "anchor_seg": "seg01", "def_block": "FN1"}],
        verse_store=[
            {"vid": "vOuter", "placeholder": V_PH_OUTER, "context": "body",
             "parent_block": "vblockA", "mount": "block", "fnrefs": [1]},
            {"vid": "vInner", "placeholder": V_PH_INNER, "context": "footnote",
             "parent_block": "FN1", "mount": "embedded", "fnrefs": []},
        ],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "vblockA", "order_index": 0, "plain_text": V_PH_OUTER}],
        footnotes=[{"n": 1, "source_text": f"A note quoting another poem: {V_PH_INNER}"}],
        verses=[
            {"vid": "vOuter", "placeholder": V_PH_OUTER, "parent_block": "vblockA"},
            {"vid": "vInner", "placeholder": V_PH_INNER, "parent_block": "FN1"},
        ],
    )
    write_draft(
        root, "seg01",
        blocks={"vblockA": V_PH_OUTER},
        footnotes={"1": f"A note quoting another poem: {V_PH_INNER}"},
        verses={"vOuter": {}, "vInner": {}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"case-g under skip must succeed (no orphan_footnote_def AND no "
        f"orphan_verse):\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    ns = read_nodestream(root)
    all_verse_vids = {v["vid"] for n in ns["nodes"] for v in n["verses"]}
    assert "vInner" not in all_verse_vids, (
        "the def-embedded verse is stripped-not-rendered -- never a node's verse"
    )
    assert 1 not in {fn["n"] for fn in ns["footnotes"]}
    md = _all_rendered_markdown(root)
    assert "[^1]:" not in md, f"no dangling footnote definition may render -- got:\n{md}"
    assert "⟦" not in md and "⟧" not in md, f"no raw sentinel may leak into render -- got:\n{md}"


def test_skip_mode_deeper_chain_footnote_def_verse_cites_footnote_succeeds(tmp_path):
    """Fix B convergence across depth WITHOUT a Fix-B worklist: vOuter cites
    fn1; fn1's def embeds vInner; vInner's (voided) source content cites fn2;
    fn2's def embeds vInner2. Every footnote in the chain is INDEPENDENTLY
    exempted by the flat orphan_footnote_def loop (each on its own
    manifest-ground-truth condition, order-independent), and each exemption
    scans its own def text -> the whole chain of verses is marked referenced."""
    root = make_root(tmp_path, verse_mode="skip")
    write_manifest(
        root,
        blocks={
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 0,
                        "plain_text": V_PH_OUTER},
            "FN1": {"type": "FN", "seg": None, "order_index": 1,
                    "plain_text": f"Note one quoting: {V_PH_INNER}"},
            "FN2": {"type": "FN", "seg": None, "order_index": 2,
                    "plain_text": f"Note two quoting: {V_PH_INNER2}"},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["vblockA"], "word_count": 10}],
        footnotes=[
            {"n": 1, "anchor_block": "vblockA", "anchor_seg": "seg01", "def_block": "FN1"},
            {"n": 2, "anchor_block": "FN1", "anchor_seg": "seg01", "def_block": "FN2"},
        ],
        verse_store=[
            {"vid": "vOuter", "placeholder": V_PH_OUTER, "context": "body",
             "parent_block": "vblockA", "mount": "block", "fnrefs": [1]},
            {"vid": "vInner", "placeholder": V_PH_INNER, "context": "footnote",
             "parent_block": "FN1", "mount": "embedded", "fnrefs": [2]},
            {"vid": "vInner2", "placeholder": V_PH_INNER2, "context": "footnote",
             "parent_block": "FN2", "mount": "embedded", "fnrefs": []},
        ],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "vblockA", "order_index": 0, "plain_text": V_PH_OUTER}],
        footnotes=[{"n": 1, "source_text": f"Note one quoting: {V_PH_INNER}"},
                   {"n": 2, "source_text": f"Note two quoting: {V_PH_INNER2}"}],
        verses=[
            {"vid": "vOuter", "placeholder": V_PH_OUTER, "parent_block": "vblockA"},
            {"vid": "vInner", "placeholder": V_PH_INNER, "parent_block": "FN1"},
            {"vid": "vInner2", "placeholder": V_PH_INNER2, "parent_block": "FN2"},
        ],
    )
    write_draft(
        root, "seg01",
        blocks={"vblockA": V_PH_OUTER},
        footnotes={"1": f"Note one quoting: {V_PH_INNER}",
                   "2": f"Note two quoting: {V_PH_INNER2}"},
        verses={"vOuter": {}, "vInner": {}, "vInner2": {}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"a deeper skip-voided chain must converge via the flat exemption "
        f"loop:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    ns = read_nodestream(root)
    all_verse_vids = {v["vid"] for n in ns["nodes"] for v in n["verses"]}
    # vOuter is a mount:block verse -> a node verse (with voided {} content
    # under skip); only the def-embedded vInner/vInner2 are stripped-not-rendered.
    assert all_verse_vids == {"vOuter"}, (
        "only the mount:block outer verse is a node verse; the def-embedded "
        "chain verses are stripped-not-rendered"
    )


def test_skip_mode_orphan_verse_extension_is_scoped_not_a_blanket_bypass(tmp_path):
    """Companion to the case-g success test: the orphan_verse extension only
    marks verses actually embedded in an EXEMPTED footnote's def text. A verse
    whose placeholder appears in NO block and NO exempted footnote's def
    (vGhost here -- registered with real draft coverage, parented to FN1 whose
    def never mentions it) must still fatally raise orphan_verse under skip."""
    root = make_root(tmp_path, verse_mode="skip")
    write_manifest(
        root,
        blocks={
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 0,
                        "plain_text": V_PH_OUTER},
            "FN1": {"type": "FN", "seg": None, "order_index": 1,
                    "plain_text": "A note that never mentions the ghost verse."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["vblockA"], "word_count": 10}],
        footnotes=[{"n": 1, "anchor_block": "vblockA", "anchor_seg": "seg01", "def_block": "FN1"}],
        verse_store=[
            {"vid": "vOuter", "placeholder": V_PH_OUTER, "context": "body",
             "parent_block": "vblockA", "mount": "block", "fnrefs": [1]},
            {"vid": "vGhost", "placeholder": V_PH_GHOST, "context": "footnote",
             "parent_block": "FN1", "mount": "embedded", "fnrefs": []},
        ],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "vblockA", "order_index": 0, "plain_text": V_PH_OUTER}],
        footnotes=[{"n": 1, "source_text": "A note that never mentions the ghost verse."}],
        verses=[
            {"vid": "vOuter", "placeholder": V_PH_OUTER, "parent_block": "vblockA"},
            {"vid": "vGhost", "placeholder": V_PH_GHOST, "parent_block": "FN1"},
        ],
    )
    write_draft(
        root, "seg01",
        blocks={"vblockA": V_PH_OUTER},
        # FN1's def text (translated) never mentions vGhost's placeholder.
        footnotes={"1": "A translated note that never mentions the ghost verse."},
        verses={"vOuter": {}, "vGhost": {}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 1, (
        f"a verse embedded in NO exempted footnote's def must still be a fatal "
        f"orphan_verse under skip:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert payload.get("reason") == "orphan_verse" or "orphan" in payload["error"].lower()
    assert "vGhost" in payload["error"]


def test_footnote_cited_in_def_embedded_verse_content_is_discovered_not_orphan(tmp_path):
    """Fix C literal issue scenario (#118 item 2), NON-skip: p1's embedded verse
    vA cites fn1; fn1's def embeds a SECOND verse vFoot; vFoot's OWN translated
    content cites fn2. Pre-fix nothing scans vFoot's content -> fn2 is invisible
    and fatals orphan_footnote_def; post-fix the recursive shared helper
    discovers it. fn2 is referenced-ONLY: present in the book-wide footnotes[]
    table but in NO node's fnrefs, and its inner verse vFoot is
    stripped-not-rendered -- so no raw ⟦FNREF_2⟧ and no dangling [^2]: appears
    anywhere in the rendered output."""
    v_ph_a = "⟦VERSE_vA_abc12345⟧"
    v_ph_foot = "⟦VERSE_vFoot_cafe1234⟧"
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                   "plain_text": f"Body text {v_ph_a} here."},
            "FN1": {"type": "FN", "seg": None, "order_index": 1,
                    "plain_text": f"A cited couplet: {v_ph_foot}"},
            "FN2": {"type": "FN", "seg": None, "order_index": 2,
                    "plain_text": "The second, deeper footnote."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
        footnotes=[
            {"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"},
            {"n": 2, "anchor_block": "FN1", "anchor_seg": "seg01", "def_block": "FN2"},
        ],
        verse_store=[
            {"vid": "vA", "placeholder": v_ph_a, "context": "body",
             "parent_block": "p1", "mount": "embedded"},
            {"vid": "vFoot", "placeholder": v_ph_foot, "context": "footnote",
             "parent_block": "FN1", "mount": "embedded"},
        ],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "p1", "order_index": 0, "plain_text": f"Body text {v_ph_a} here."}],
        footnotes=[{"n": 1, "source_text": f"A cited couplet: {v_ph_foot}"},
                   {"n": 2, "source_text": "The second, deeper footnote."}],
        verses=[
            {"vid": "vA", "placeholder": v_ph_a, "parent_block": "p1"},
            {"vid": "vFoot", "placeholder": v_ph_foot, "parent_block": "FN1"},
        ],
    )
    write_draft(
        root, "seg01",
        blocks={"p1": f"Translated body {v_ph_a} here."},
        footnotes={"1": f"A cited couplet: {v_ph_foot}", "2": "The translated deeper footnote."},
        verses={
            "vA": {"rendered": f"Rendered line {FN_PH_1}", "literal_gloss": "Gloss, no ref"},
            "vFoot": {"rendered": f"Embedded couplet {FN_PH_2}", "literal_gloss": "Gloss embedded"},
        },
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"a footnote cited only inside a def-embedded verse's content must be "
        f"discovered, not orphaned:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    ns = read_nodestream(root)
    by_id = {n["id"]: n for n in ns["nodes"]}
    assert by_id["p1"]["fnrefs"] == [1], (
        "fn1 (cited in the RENDERED verse vA) belongs in the carrier node's "
        "fnrefs; fn2 (reached only through the stripped inner verse) must NOT"
    )
    fn_ns = {fn["n"] for fn in ns["footnotes"]}
    assert fn_ns == {1, 2}, "both footnotes' text must land in the book-wide table"
    assert all(2 not in n["fnrefs"] for n in ns["nodes"]), (
        "fn2 is referenced-only -- it must appear in NO node's fnrefs"
    )
    all_verse_vids = {v["vid"] for n in ns["nodes"] for v in n["verses"]}
    assert all_verse_vids == {"vA"}, "the inner def-embedded verse vFoot is never rendered"
    md = _all_rendered_markdown(root)
    assert FN_PH_2 not in md and "⟦" not in md, f"no raw sentinel may leak -- got:\n{md}"
    assert "[^2]:" not in md, f"fn2, referenced by no node, must render no [^2]: -- got:\n{md}"


def test_footnote_nested_two_levels_deep_in_def_embedded_verses_is_discovered(tmp_path):
    """Fix C genuine fixed-point recursion (NON-skip), one level deeper than the
    test above: fn2's def ALSO embeds a verse vFoot2 whose content cites fn3.
    Proves the discovery is a true fixed point, not a hardcoded one-extra-level
    patch. fn3 lands referenced-only (book footnotes[], no node fnrefs)."""
    v_ph_a = "⟦VERSE_vA_abc12345⟧"
    v_ph_foot = "⟦VERSE_vFoot_cafe1234⟧"
    v_ph_foot2 = "⟦VERSE_vFoot2_beef5678⟧"
    root = make_root(tmp_path)
    write_manifest(
        root,
        blocks={
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 0,
                   "plain_text": f"Body text {v_ph_a} here."},
            "FN1": {"type": "FN", "seg": None, "order_index": 1,
                    "plain_text": f"A cited couplet: {v_ph_foot}"},
            "FN2": {"type": "FN", "seg": None, "order_index": 2,
                    "plain_text": f"A deeper couplet: {v_ph_foot2}"},
            "FN3": {"type": "FN", "seg": None, "order_index": 3,
                    "plain_text": "The third, deepest footnote."},
        },
        segments=[{"seg": "seg01", "kind": "body", "title_text": "Ch1",
                   "block_ids": ["p1"], "word_count": 10}],
        footnotes=[
            {"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"},
            {"n": 2, "anchor_block": "FN1", "anchor_seg": "seg01", "def_block": "FN2"},
            {"n": 3, "anchor_block": "FN2", "anchor_seg": "seg01", "def_block": "FN3"},
        ],
        verse_store=[
            {"vid": "vA", "placeholder": v_ph_a, "context": "body",
             "parent_block": "p1", "mount": "embedded"},
            {"vid": "vFoot", "placeholder": v_ph_foot, "context": "footnote",
             "parent_block": "FN1", "mount": "embedded"},
            {"vid": "vFoot2", "placeholder": v_ph_foot2, "context": "footnote",
             "parent_block": "FN2", "mount": "embedded"},
        ],
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "p1", "order_index": 0, "plain_text": f"Body text {v_ph_a} here."}],
        footnotes=[{"n": 1, "source_text": f"A cited couplet: {v_ph_foot}"},
                   {"n": 2, "source_text": f"A deeper couplet: {v_ph_foot2}"},
                   {"n": 3, "source_text": "The third, deepest footnote."}],
        verses=[
            {"vid": "vA", "placeholder": v_ph_a, "parent_block": "p1"},
            {"vid": "vFoot", "placeholder": v_ph_foot, "parent_block": "FN1"},
            {"vid": "vFoot2", "placeholder": v_ph_foot2, "parent_block": "FN2"},
        ],
    )
    write_draft(
        root, "seg01",
        blocks={"p1": f"Translated body {v_ph_a} here."},
        footnotes={"1": f"A cited couplet: {v_ph_foot}",
                   "2": f"A deeper couplet: {v_ph_foot2}",
                   "3": "The translated deepest footnote."},
        verses={
            "vA": {"rendered": f"Rendered line {FN_PH_1}", "literal_gloss": "Gloss"},
            "vFoot": {"rendered": f"Embedded couplet {FN_PH_2}", "literal_gloss": "Gloss"},
            "vFoot2": {"rendered": f"Deeper couplet {FN_PH_3}", "literal_gloss": "Gloss"},
        },
    )
    write_ledger(root, {"seg01": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"a two-levels-deep nested footnote must be discovered by the "
        f"fixed-point recursion:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    ns = read_nodestream(root)
    fn_ns = {fn["n"] for fn in ns["footnotes"]}
    assert fn_ns == {1, 2, 3}, "all three footnotes' text must land in the book-wide table"
    assert all(2 not in n["fnrefs"] and 3 not in n["fnrefs"] for n in ns["nodes"]), (
        "fn2 and fn3 are referenced-only -- neither may appear in any node's fnrefs"
    )
    all_verse_vids = {v["vid"] for n in ns["nodes"] for v in n["verses"]}
    assert all_verse_vids == {"vA"}, "only the outer rendered verse is a node verse"


# ===========================================================================
# 15. #210 -- manifest-declared heading_types classification.
#
# _classify_kind's sole heading test was the literal block type "HEAD"; a
# custom extractor's own heading tag (never "HEAD") silently classified
# prose. manifest.heading_types (schema-optional, additive) lets a manifest
# DECLARE which of its own block types are headings too. Through the REAL
# classifier via a real assemble.py subprocess -- NOT render_obsidian.
# test.py's in-process make_node(kind="heading") helper, which hand-builds
# the property and would mask this exact bug.
# ===========================================================================


def _book_with_custom_heading_block_type(root, heading_types=None, heading_levels=None):
    """Two segments, each with one CHAPTER-typed block (the candidate
    heading) followed by one ordinary PARA block. CHAPTER is never "HEAD" --
    only `heading_types` (when passed) can make it classify as a heading.
    `heading_levels`: optional (#210 R1) -- forwarded straight through."""
    write_manifest(
        root,
        blocks={
            "h1": {"type": "CHAPTER", "seg": "seg01", "order_index": 0,
                   "plain_text": "Raw Chapter One Heading"},
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 1,
                   "plain_text": "Body text one."},
            "h2": {"type": "CHAPTER", "seg": "seg02", "order_index": 2,
                   "plain_text": "Raw Chapter Two Heading"},
            "p2": {"type": "PARA", "seg": "seg02", "order_index": 3,
                   "plain_text": "Body text two."},
        },
        segments=[
            {"seg": "seg01", "kind": "body", "title_text": "Chapter One",
             "block_ids": ["h1", "p1"], "word_count": 20},
            {"seg": "seg02", "kind": "body", "title_text": "Chapter Two",
             "block_ids": ["h2", "p2"], "word_count": 20},
        ],
        heading_types=heading_types,
        heading_levels=heading_levels,
    )
    write_segpack(
        root, "seg01",
        blocks=[{"id": "h1", "order_index": 0, "plain_text": "Raw Chapter One Heading"},
                {"id": "p1", "order_index": 1, "plain_text": "Body text one."}],
    )
    write_segpack(
        root, "seg02",
        blocks=[{"id": "h2", "order_index": 0, "plain_text": "Raw Chapter Two Heading"},
                {"id": "p2", "order_index": 1, "plain_text": "Body text two."}],
    )
    write_draft(
        root, "seg01",
        blocks={"h1": "Translated Chapter One Heading", "p1": "Translated body text one."},
    )
    write_draft(
        root, "seg02",
        blocks={"h2": "Translated Chapter Two Heading", "p2": "Translated body text two."},
    )
    write_ledger(root, {"seg01": {"status": "converged"}, "seg02": {"status": "converged"}})


def test_manifest_declared_heading_type_classifies_as_heading_node(tmp_path):
    """RED pre-#210: raw_type "CHAPTER" is not "HEAD", so _classify_kind
    returns "prose" even with heading_types declared -- this is the
    regression lock proving the manifest's declared set is actually wired
    through build_nodestream into _classify_kind."""
    root = make_root(tmp_path)
    _book_with_custom_heading_block_type(root, heading_types=["CHAPTER"])

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    ns = read_nodestream(root)
    by_id = {n["id"]: n for n in ns["nodes"]}
    assert by_id["h1"]["kind"] == "heading", (
        "a block whose type is listed in manifest.heading_types must "
        "classify as a heading, same as the built-in \"HEAD\""
    )
    assert by_id["h2"]["kind"] == "heading"
    # Ordinary PARA blocks are unaffected -- heading_types only ever ADDS
    # heading matches, it never widens what counts as prose/verse.
    assert by_id["p1"]["kind"] == "prose"
    assert by_id["p2"]["kind"] == "prose"


def test_custom_type_without_heading_types_declaration_stays_prose_back_compat(tmp_path):
    """Negative/back-compat: the exact same CHAPTER-typed blocks, but
    manifest.heading_types is entirely absent (not even an empty list) --
    must classify prose, byte-identical to pre-#210 behavior. Also proves
    the render-level fallback: with no heading node, _segment_title falls
    back to the raw seg id, not the CHAPTER block's text."""
    root = make_root(tmp_path)
    _book_with_custom_heading_block_type(root, heading_types=None)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert "heading_types" not in manifest, "sanity: the field must be genuinely absent"

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    ns = read_nodestream(root)
    by_id = {n["id"]: n for n in ns["nodes"]}
    assert by_id["h1"]["kind"] == "prose"
    assert by_id["h2"]["kind"] == "prose"

    note_path = root / "out" / "001 seg01.md"
    assert note_path.is_file(), (
        f"with no heading node, the segment note must fall back to the raw "
        f"seg id for both title and filename -- files present: "
        f"{sorted(p.name for p in (root / 'out').glob('*.md'))}"
    )
    assert "## Raw Chapter One Heading" not in note_path.read_text(encoding="utf-8")


def test_manifest_declared_heading_type_drives_render_title_and_filename(tmp_path):
    """End-to-end (contract §10, via the real obsidian adapter): a declared
    heading block's DRAFT text becomes the rendered "## " markdown heading
    AND drives the segment note's frontmatter title and filename -- not the
    raw seg id (render_obsidian.py's _segment_title / out-of-the-box
    filename slug both key off the first kind=="heading" node)."""
    root = make_root(tmp_path)
    _book_with_custom_heading_block_type(root, heading_types=["CHAPTER"])

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    note_path = root / "out" / "001 Translated Chapter One Heading.md"
    assert note_path.is_file(), (
        f"expected the heading text (not the raw seg id) to drive the "
        f"filename -- files present: "
        f"{sorted(p.name for p in (root / 'out').glob('*.md'))}"
    )
    note_text = note_path.read_text(encoding="utf-8")
    assert "## Translated Chapter One Heading" in note_text, (
        "the declared-heading block's draft text must render as a markdown "
        "heading in the segment note body"
    )
    assert "title: Translated Chapter One Heading" in note_text, (
        "the declared-heading block's draft text must drive the frontmatter "
        "title, not the raw seg id"
    )
    assert "title: seg01" not in note_text, (
        "the raw seg id must not leak into the frontmatter title once a "
        "real heading is present"
    )


# ===========================================================================
# 17. #210 R1 -- manifest-declared heading_levels (markdown heading LEVEL).
#
# Pre-#210-R1, render_obsidian.py hardcoded every heading node to "## " --
# a custom adapter declaring both a chapter type and a sub-section type got
# both flattened to the same level. manifest.heading_levels (schema-
# optional, additive, keyed by the SAME raw block-type tags heading_types
# declares, plus the always-heading built-in "HEAD") lets a manifest assign
# each heading type its own level (1-6). assemble.py reads/validates it and
# stamps a "level" key onto every node; render_obsidian.py (A2's own file,
# not this one) is what actually emits the "#"*level markdown -- this file
# only proves the node's own "level" field is correct, not the rendered
# bytes.
#
# The cross-field rule (every heading_levels key must be a member of
# heading_types union {"HEAD"}) is NOT expressible in JSON Schema, so it is
# enforced independently in TWO places: validate_extraction.py at W2 (its
# own test file), and HERE in assemble.py itself -- because assemble.py is
# directly reachable on a resumed project whose manifest.json may never
# have passed through W2's jsonschema gate (or was hand-edited after). The
# AssembleError tests below prove assemble.py does not trust W2 to have run.
# ===========================================================================


def _book_with_heading_levels(root, heading_types=None, heading_levels=None):
    """One segment mixing every kind _classify_kind can produce: the
    built-in "HEAD" heading, a declared "CHAPTER" heading, a declared-but-
    unmapped "PART" heading, an ordinary PARA (prose), and a block-mounted
    VERSE. Lets one fixture cover "heading gets a level", "declared type
    absent from heading_levels defaults to 2", and "non-heading kinds carry
    level: None" all at once."""
    write_manifest(
        root,
        blocks={
            "hHead": {"type": "HEAD", "seg": "seg01", "order_index": 0,
                      "plain_text": "Raw Head Heading"},
            "hChapter": {"type": "CHAPTER", "seg": "seg01", "order_index": 1,
                         "plain_text": "Raw Chapter Heading"},
            "hPart": {"type": "PART", "seg": "seg01", "order_index": 2,
                      "plain_text": "Raw Part Heading"},
            "p1": {"type": "PARA", "seg": "seg01", "order_index": 3,
                   "plain_text": "Body text one."},
            "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 4,
                        "plain_text": V_PH_A},
        },
        segments=[
            {"seg": "seg01", "kind": "body", "title_text": "Chapter One",
             "block_ids": ["hHead", "hChapter", "hPart", "p1", "vblockA"],
             "word_count": 50},
        ],
        verse_store=[{"vid": "vA", "placeholder": V_PH_A, "context": "body",
                       "parent_block": "vblockA", "mount": "block"}],
        heading_types=heading_types,
        heading_levels=heading_levels,
    )
    write_segpack(
        root, "seg01",
        blocks=[
            {"id": "hHead", "order_index": 0, "plain_text": "Raw Head Heading"},
            {"id": "hChapter", "order_index": 1, "plain_text": "Raw Chapter Heading"},
            {"id": "hPart", "order_index": 2, "plain_text": "Raw Part Heading"},
            {"id": "p1", "order_index": 3, "plain_text": "Body text one."},
            {"id": "vblockA", "order_index": 4, "source_html": "<p>Verse line one<br/>Verse line two</p>"},
        ],
        verses=[{"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"}],
    )
    write_draft(
        root, "seg01",
        blocks={
            "hHead": "Translated Head Heading",
            "hChapter": "Translated Chapter Heading",
            "hPart": "Translated Part Heading",
            "p1": "Translated body text one.",
            "vblockA": V_PH_A,
        },
        verses={"vA": {"rendered": "Line one\nLine two", "literal_gloss": "Gloss for one and two"}},
    )
    write_ledger(root, {"seg01": {"status": "converged"}})


def test_heading_gets_level_prose_and_verse_get_none(tmp_path):
    """heading_levels maps "HEAD" and "CHAPTER" to distinct levels; "PART" is
    declared in heading_types but deliberately left OUT of heading_levels
    (must default to 2, not error). Prose and verse nodes must carry
    level: None -- level is a heading-only concept."""
    root = make_root(tmp_path)
    _book_with_heading_levels(
        root, heading_types=["CHAPTER", "PART"],
        heading_levels={"HEAD": 1, "CHAPTER": 3},
    )

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    ns = read_nodestream(root)
    by_id = {n["id"]: n for n in ns["nodes"]}
    # Presence, not just value equality, over EVERY node -- a future
    # refactor that drops the key on some path must fail here even if
    # every value assertion below still happens to hold.
    for node in ns["nodes"]:
        assert "level" in node, f"node {node.get('id')!r} is missing the \"level\" key entirely"
    assert by_id["hHead"]["kind"] == "heading"
    assert by_id["hHead"]["level"] == 1
    assert by_id["hChapter"]["kind"] == "heading"
    assert by_id["hChapter"]["level"] == 3
    assert by_id["hPart"]["kind"] == "heading", (
        "PART is declared in heading_types, so it must still classify as "
        "a heading even though heading_levels omits it"
    )
    assert by_id["hPart"]["level"] == 2, (
        "a heading type absent from heading_levels must default to level "
        "2 -- byte-identical to pre-#210-R1 output"
    )
    assert by_id["p1"]["kind"] == "prose"
    assert by_id["p1"]["level"] is None, "level is a heading-only concept"
    assert by_id["vblockA"]["kind"] == "verse"
    assert by_id["vblockA"]["level"] is None, "level is a heading-only concept"


def test_heading_levels_absent_defaults_every_heading_to_level_2(tmp_path):
    """The exact same book, but manifest.heading_levels is entirely absent
    (not even an empty object) -- every heading node (built-in HEAD and the
    declared CHAPTER/PART types alike) must default to level 2, matching
    the pre-#210-R1 hardcoded "## " output byte-for-byte."""
    root = make_root(tmp_path)
    _book_with_heading_levels(
        root, heading_types=["CHAPTER", "PART"], heading_levels=None,
    )
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert "heading_levels" not in manifest, "sanity: the field must be genuinely absent"

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    ns = read_nodestream(root)
    by_id = {n["id"]: n for n in ns["nodes"]}
    assert by_id["hHead"]["level"] == 2
    assert by_id["hChapter"]["level"] == 2
    assert by_id["hPart"]["level"] == 2


def test_heading_level_key_not_declared_in_heading_types_is_fatal(tmp_path):
    """Cross-field guard, proven INDEPENDENTLY of W2 (this is a real
    assemble.py subprocess, never run through validate_extraction.py's own
    jsonschema gate first): a heading_levels key ("SECTION") that is
    neither in heading_types nor the built-in "HEAD" is a typo that would
    otherwise silently no-op -- must raise AssembleError, exit 1."""
    root = make_root(tmp_path)
    _book_with_custom_heading_block_type(
        root, heading_types=["CHAPTER"], heading_levels={"SECTION": 2},
    )

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload.get("reason") == "undeclared_heading_level_type", payload
    assert "SECTION" in payload.get("error", ""), payload
    assert not (root / "out" / ".assembled" / "nodestream.json").is_file()


def test_heading_level_non_int_value_is_fatal(tmp_path):
    """A heading_levels value that is not an int (a string digit, here) must
    raise AssembleError -- assemble.py cannot trust the manifest was ever
    jsonschema-validated."""
    root = make_root(tmp_path)
    _book_with_custom_heading_block_type(
        root, heading_types=["CHAPTER"], heading_levels={"CHAPTER": "2"},
    )

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert "heading_levels" in payload.get("error", ""), payload


def test_heading_level_bool_value_is_fatal(tmp_path):
    """`bool` is a subclass of `int` in Python -- `isinstance(True, int)` is
    True -- so a naive `isinstance(v, int)` check would silently accept
    `heading_levels: {"CHAPTER": true}` as level 1. Must be rejected
    explicitly, same as validate_extraction.py's own W2 schema gate."""
    root = make_root(tmp_path)
    _book_with_custom_heading_block_type(
        root, heading_types=["CHAPTER"], heading_levels={"CHAPTER": True},
    )

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert "heading_levels" in payload.get("error", ""), payload


@pytest.mark.parametrize("bad_level", [0, 7])
def test_heading_level_out_of_range_value_is_fatal(tmp_path, bad_level):
    """A heading_levels value outside 1..6 must raise AssembleError --
    level 0 would render zero "#" characters (silently producing prose,
    not a heading), level 7 is not valid markdown."""
    root = make_root(tmp_path)
    _book_with_custom_heading_block_type(
        root, heading_types=["CHAPTER"], heading_levels={"CHAPTER": bad_level},
    )

    result = run_assemble(root)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert "heading_levels" in payload.get("error", ""), payload
    assert str(bad_level) in payload.get("error", ""), payload


# ===========================================================================
# N. Default-on mentions_section: FAIL-CLOSED, ALWAYS.
#
# make_root()'s default profile targets "obsidian" and writes NO
# mentions_section block at all -- under the 1.10.0 default-on flip that
# makes the flag effective-enabled but only IMPLIED (never explicit
# `enabled: true`). make_root() also deliberately copies only
# assemble.py/output_resolve.py/render_obsidian.py/validate_draft.py into
# scripts/ (§3.8's "under-provisioned durable_root" shape -- the real shape
# a hand-scaffolded root takes), so bootstrap_names.py/canon_senses.py/
# occurrence_targets.py are NOT importable there.
#
# An earlier revision here gave an IMPLIED flag an advisory skip-with-
# warning instead of a hard failure (§O2a). Codex review (MAJOR-2,
# user-ratified) found that posture didn't actually hold end to end:
# validate_backlinks.py -- the LAST step of the same W9 chain, gated on the
# SAME default-on predicate -- has no implied-vs-explicit distinction and
# unconditionally `_fatal`s (exit 2) on an identical broken dependency. So
# the "never brick at W9" goal wasn't achieved; it just moved the brick one
# step later. The fix: match validate_backlinks.py's existing fail-closed
# posture. Both tests below now assert the SAME failure mode regardless of
# implied vs. explicit -- that symmetry is the point.
# ===========================================================================


def test_implied_flag_also_fails_closed(tmp_path):
    """mentions_section only IMPLIED (`mentions_enabled=None` -- the key is
    OMITTED entirely, target: obsidian) on an under-provisioned root (no
    bootstrap_names.py/canon_senses.py/occurrence_targets.py under
    scripts/) must fail closed -- exactly like an explicit `enabled: true`
    on the same root (see the paired test below). RED against the prior
    graceful-degrade code: it asserted returncode == 0 with an advisory
    WARNING and no 'mentions' key; this fixture is otherwise
    byte-identical to that prior test."""
    root = make_root(tmp_path, mentions_enabled=None)
    build_clean_two_segment_book(root)
    write_ledger(root, {"seg01": {"status": "converged"}, "seg02": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 2, (
        f"an IMPLIED mentions_section flag must fail closed on an "
        f"under-provisioned root, same as an explicit one:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload.get("reason") == "dependency_precondition", payload
    assert not (root / "out" / ".assembled" / "nodestream.json").is_file(), (
        "a fail-closed precondition must halt BEFORE nodestream.json is "
        "persisted"
    )


def test_explicit_flag_also_fails_closed(tmp_path):
    """The mirror of the test above: the IDENTICAL under-provisioned root,
    but mentions_section.enabled: true written EXPLICITLY
    (`mentions_enabled=True`). Both this and the implied case above must
    produce the SAME failure -- there is no longer a behavioral
    distinction between them."""
    root = make_root(tmp_path, mentions_enabled=True)
    build_clean_two_segment_book(root)
    write_ledger(root, {"seg01": {"status": "converged"}, "seg02": {"status": "converged"}})

    result = run_assemble(root)
    assert result.returncode == 2, (
        f"an EXPLICIT mentions_section.enabled: true must fail closed on an "
        f"under-provisioned root:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload.get("reason") == "dependency_precondition", payload
    assert not (root / "out" / ".assembled" / "nodestream.json").is_file(), (
        "a fail-closed precondition must halt BEFORE nodestream.json is "
        "persisted"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
