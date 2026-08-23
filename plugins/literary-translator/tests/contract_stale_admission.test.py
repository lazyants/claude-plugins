"""tests/contract_stale_admission.test.py -- #533: a unit that went stale
ONLY because the style contract moved can be admitted by declaration.

R9 (shipped 1.26.0) says a style-contract edit applies FORWARD: text already
verified stays verified and no back-sweep is owed. The tooling disagreed --
`style_contract_hash` sits outside `SAFE_STALE_CARVEOUT_FIELDS`, so every unit
converged before the edit blocked both the W7 completeness gate and W9
assembly until it was reviewed again. This file pins the opt-in that lets the
code agree with the rule, and -- more importantly -- pins everything the
opt-in must still refuse.

## What is covered here, and what is covered elsewhere

Four scripts read the same declaration and must never disagree about the same
record:

  - `assemble.py` (W9 gate) -- driven here as a real subprocess.
  - `validate_assembled.py` (default-scope delivery gate) -- driven here as a
    real subprocess.
  - `validate_conservation.py` (WARN-only output-coverage lane, whose
    population IS validate_assembled.py's) -- driven here through the shared
    helper it actually calls.
  - `final_audit.py` (W7 completeness gate, the one W9 is gated on) -- its
    PURE predicate and arithmetic are pinned here; its end-to-end CLI
    behaviour is pinned in tests/final_audit.test.py, which already owns the
    heavy durable-root harness (real cache_key.py runs, a real
    select_segments.py classification) that a genuine cache-key move needs.
    Stated explicitly so the split is not mistaken for a gap.

## Fixture strategy

Two self-contained harnesses, each duplicated from the sibling that owns it
rather than imported (house convention: each *.test.py stays self-contained):
tests/stale_carveout.test.py's assemble.py harness, and
tests/validate_assembled_carveout.test.py's validate_assembled.py harness.
Both gain one parameter -- whether profile.yml carries the declaration -- and
are otherwise unchanged.
"""
import hashlib
import importlib.util
import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"

ASSEMBLE_SRC = SCRIPTS_SRC_DIR / "assemble.py"
FINAL_AUDIT_SRC = SCRIPTS_SRC_DIR / "final_audit.py"
VALIDATE_ASSEMBLED_SRC = SCRIPTS_SRC_DIR / "validate_assembled.py"
VALIDATE_CONSERVATION_SRC = SCRIPTS_SRC_DIR / "validate_conservation.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
OUTPUT_RESOLVE_SRC = SCRIPTS_SRC_DIR / "output_resolve.py"
RENDER_OBSIDIAN_SRC = SCRIPTS_SRC_DIR / "render_obsidian.py"
CACHE_KEY_SRC = SCRIPTS_SRC_DIR / "cache_key.py"

for _src in (
    ASSEMBLE_SRC, FINAL_AUDIT_SRC, VALIDATE_ASSEMBLED_SRC,
    VALIDATE_CONSERVATION_SRC, VALIDATE_DRAFT_SRC, OUTPUT_RESOLVE_SRC,
    RENDER_OBSIDIAN_SRC, CACHE_KEY_SRC,
):
    assert _src.is_file(), f"required source not found: {_src}"

CONTRACT_FIELD = "style_contract_hash"
MACHINERY_FIELDS = ("plugin_bundle_hash", "schema_hash", "derivation_bundle_hash")


def _load_module_from_source(src_path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, src_path)
    assert spec is not None and spec.loader is not None, f"cannot load spec for {src_path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_real_final_audit():
    """final_audit.py loaded IN PLACE -- only ever used here to call pure
    functions with test-supplied dicts (no filesystem read at all), matching
    tests/final_audit.test.py's own load_final_audit_module() convention."""
    return _load_module_from_source(FINAL_AUDIT_SRC, "contract_admission__final_audit_ref")


def load_real_assemble():
    return _load_module_from_source(ASSEMBLE_SRC, "contract_admission__assemble_ref")


def load_real_validate_assembled():
    return _load_module_from_source(
        VALIDATE_ASSEMBLED_SRC, "contract_admission__validate_assembled_ref"
    )


# ===========================================================================
# The forbidden-field population, derived from cache_key.py itself rather
# than hand-listed. A hand-typed list here would freeze the very set it is
# meant to police: a cache-key field added later would be absent from the
# matrix and silently untested.
# ===========================================================================


def content_affecting_cache_key_fields():
    ck = _load_module_from_source(CACHE_KEY_SRC, "contract_admission__cache_key_ref")
    fields = [
        f
        for f in ck.CACHE_KEY_FIELD_ORDER
        if f not in MACHINERY_FIELDS and f != CONTRACT_FIELD
    ]
    assert len(fields) >= 10, (
        f"expected the content-affecting cache-key population to be "
        f"substantial; got {fields!r} -- if CACHE_KEY_FIELD_ORDER shrank this "
        f"much, this matrix is no longer testing what it claims to"
    )
    return fields


CONTENT_AFFECTING_FIELDS = content_affecting_cache_key_fields()


# ===========================================================================
# Harness A -- assemble.py (duplicated from tests/stale_carveout.test.py,
# plus the `admit` parameter)
# ===========================================================================

# #492 retired the hand-written DUMMY_CACHE_KEY that used to sit here:
# assembly now recomputes every content-affecting cache-key field from the
# live durable_root, so a fabricated stored key is a guaranteed refusal rather
# than an inert schema-shaped placeholder. real_cache_key() below produces the
# genuine one by running the shipped cache_key.py.


def _yaml_dump(obj) -> str:
    return yaml.safe_dump(obj, sort_keys=False)


def assemble_profile(admit=None):
    """`admit=None` omits the key entirely (the shape every existing project
    has today); True/False write it explicitly. The three shapes are
    deliberately distinguishable: 'absent' and 'explicitly false' must behave
    identically, and a test that could only express one of them could not
    prove that."""
    profile = {
        "profile_version": 1,
        "project": {
            "title": "Fixture Book",
            "durable_root": "/placeholder",
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
        "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": None},
        "engine": {"effort": "high", "max_fix_rounds": 4, "batch_agent_cap": 1000},
        "footnotes": {"apparatus_policy": "translate_all"},
        "glossary": {"research_mode": "live"},
        "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
        "output": {
            "v1_scope": "assembled_book",
            "destination": "/placeholder/out/",
            "target": "obsidian",
            "name_display": {"parenthetical_originals": "never"},
            "adapter_config": {
                "obsidian": {"folders": {}, "mentions_section": {"enabled": False}},
                "epub": None,
                "custom": {"renderer_path": None},
            },
        },
    }
    if admit is not None:
        profile["validation"]["admit_contract_only_stale"] = admit
    return profile


def _write_cache_key_inputs(root: Path, scripts_dir: Path) -> None:
    """#492: the durable-root files cache_key.py's own field computers read.
    assemble.py now recomputes every content-affecting cache-key field from
    the live root and refuses on a mismatch, so this fixture must carry real
    inputs and a real stored key. Restated from tests/final_audit.test.py's
    make_durable_root() rather than imported -- house convention is one
    self-contained file per test module. Only style_bible.md's two
    STYLE_CONTRACT markers are load-bearing; `runs/.plugin_bundle_hash` is the
    marker Step 0a writes and cache_key.py reads back rather than re-hashing
    the bundle."""
    # Fill a gap, never clobber: whichever of these the caller already staged
    # as the REAL module wins. cache_key.py only needs the paths to exist and
    # to hash stably, so deferring to a real copy serves both purposes -- and a
    # placeholder written over a real dependency fails far from its cause
    # (verified on assemble_link_groups_wiring.test.py, whose #497 cases need
    # bootstrap_names.extract_candidate_spans).
    for _name, _body in (("bootstrap_names.py", b"# bootstrap_names.py fixture\n"),
                         ("segpack.py", b"# segpack.py fixture\n")):
        if not (scripts_dir / _name).exists():
            (scripts_dir / _name).write_bytes(_body)
    (root / "style_bible.md").write_bytes(
        b"# Style Bible\n\n<!-- STYLE_CONTRACT_BEGIN -->\n"
        b"Formal register, Oxford comma.\n<!-- STYLE_CONTRACT_END -->\n"
    )
    (root / "translate_TASK.md").write_bytes(b"TRANSLATE TASK PROMPT v1\n")
    (root / "review_TASK.md").write_bytes(b"REVIEW TASK PROMPT v1\n")
    (root / "extract.py").write_bytes(b"# extract.py fixture v1\n")
    (root / "a.txt").write_bytes(b"Ceci est un texte source de test.\n")
    languages_dir = root / "languages"
    languages_dir.mkdir(exist_ok=True)
    (languages_dir / "fr_test.json").write_text(
        json.dumps({"PARTICLES": ["de"], "STOPWORDS": ["le"], "has_elision": False,
                    "ELISION_RE": None}),
        encoding="utf-8",
    )
    (root / "schemas").mkdir(exist_ok=True)
    for _name in ("draft.schema.json", "review.schema.json", "segpack.schema.json"):
        (root / "schemas" / _name).write_bytes(b"{}\n")
    runs_dir = root / "runs"
    runs_dir.mkdir(exist_ok=True)
    (runs_dir / ".plugin_bundle_hash").write_text(
        "test-plugin-bundle-marker-v1\n", encoding="utf-8"
    )


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


def make_assemble_root(tmp_path, admit=None) -> Path:
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    # CACHE_KEY_SRC (#492): assemble.py imports it as a sibling and
    # recomputes every content-affecting field from the live root.
    for src in (ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC, VALIDATE_DRAFT_SRC,
                CACHE_KEY_SRC):
        shutil.copy2(src, scripts_dir / src.name)

    profile = assemble_profile(admit)
    profile["project"]["durable_root"] = str(root)
    profile["output"]["destination"] = str(root / "out")
    (root / "profile.yml").write_text(_yaml_dump(profile), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )
    (root / "canon.json").write_text(
        json.dumps({"entries": {}, "review_queue": []}), encoding="utf-8"
    )
    (root / "segments").mkdir()
    (root / "runs").mkdir()
    # #492: last, so it can reuse the runs/ dir just created above.
    _write_cache_key_inputs(root, scripts_dir)
    return root


def write_book_scaffold(root, seg_ids):
    blocks = {}
    segments = []
    for i, seg in enumerate(seg_ids):
        bid = f"p_{seg}"
        blocks[bid] = {
            "type": "PARA",
            "seg": seg,
            "order_index": i,
            "plain_text": f"Prose for {seg}.",
        }
        segments.append(
            {"seg": seg, "kind": "body", "title_text": seg, "block_ids": [bid], "word_count": 3}
        )
    manifest = {
        "blocks": blocks,
        "spine": [{"pos": 0, "file": "a.txt", "klass": "body"}],
        "segments": segments,
        "footnotes": [],
        "frontback": [],
        "verse": {"store": []},
        "source_inputs": ["a.txt"],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    for seg in seg_ids:
        bid = f"p_{seg}"
        pack = {
            "seg": seg,
            "title": seg,
            "kind": "body",
            "word_count": 3,
            "blocks": [{"id": bid, "order_index": 0, "plain_text": f"Prose for {seg}."}],
            "footnotes": [],
            "verses": [],
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
    """Independent, stdlib-only ground truth -- never imported from a script
    under test."""
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def write_segment_draft(root, seg, text=None) -> bytes:
    bid = f"p_{seg}"
    draft = {
        "seg": seg,
        "blocks": {bid: text or f"Translated {seg}."},
        "footnotes": {},
        "verses": {},
        "names": [],
        "notes": [],
    }
    draft_bytes = json.dumps(draft, ensure_ascii=False).encode("utf-8")
    (root / "segments" / f"{seg}.draft.json").write_bytes(draft_bytes)
    return draft_bytes


def write_ledger_segments(root, segments: dict) -> None:
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8"
    )


def converged_ledger_record(root, seg, reviewed_draft_sha1_override=None) -> dict:
    draft_doc = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    sha1 = reviewed_draft_sha1_override or draft_content_sha1_of(draft_doc)
    return {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "status": "converged",
        "rounds": 1,
        "cache_key": real_cache_key(root, seg),
        "n_blocks": 1,
        "n_footnotes": 0,
        "n_verses": 0,
        "reviewed_draft_sha1": sha1,
    }


_UNSET = object()


def stale_ledger_record(root, seg, mismatched_fields=_UNSET, reviewed_draft_sha1_override=None) -> dict:
    record = converged_ledger_record(root, seg, reviewed_draft_sha1_override)
    record["status"] = "stale"
    if mismatched_fields is not _UNSET:
        record["stale_mismatched_fields"] = mismatched_fields
    return record


def mark_sentinel_present(root, seg) -> Path:
    path = root / "segments" / f".ever_converged.{seg}"
    path.write_bytes(b"converged\n")
    return path


def mark_sentinel_ambiguous(root, seg) -> Path:
    """A dangling symlink -- AMBIGUOUS, never ABSENT."""
    path = root / "segments" / f".ever_converged.{seg}"
    path.symlink_to(root / "segments" / "no-such-target")
    return path


def run_assemble(root: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "assemble.py")],
        capture_output=True, text=True, timeout=timeout,
    )


def parse_one_json_line(proc: subprocess.CompletedProcess) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one stdout JSON line, got {len(lines)}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(lines[0])


def one_contract_stale_book(tmp_path, admit=None, mismatched=(CONTRACT_FIELD,)):
    """The shape this whole feature is about: one converged segment, one
    segment that converged and then went stale because the contract moved,
    sentinel intact, draft untouched since review."""
    root = make_assemble_root(tmp_path, admit)
    write_book_scaffold(root, ["seg01", "seg02"])
    write_segment_draft(root, "seg01")
    write_segment_draft(root, "seg02")
    mark_sentinel_present(root, "seg02")
    write_ledger_segments(
        root,
        {
            "seg01": converged_ledger_record(root, "seg01"),
            "seg02": stale_ledger_record(root, "seg02", mismatched_fields=list(mismatched)),
        },
    )
    return root


# ===========================================================================
# 1-2. Declaration ABSENT, and declaration explicitly FALSE, both refuse --
# and emit nothing new. Presence is not consent.
# ===========================================================================


@pytest.mark.parametrize("admit,label", [(None, "absent"), (False, "explicit-false")])
def test_undeclared_contract_stale_still_refuses_assembly(tmp_path, admit, label):
    """Mutation: make the new branch fire on `admit_contract_only is not
    None` (i.e. treat the KEY's presence as consent) -> the explicit-false
    row goes red while the absent row still passes, which is exactly the
    asymmetry a presence check would hide."""
    root = one_contract_stale_book(tmp_path, admit)

    result = run_assemble(root)
    assert result.returncode == 2, (
        f"[{label}] a contract-only stale unit must still block assembly when "
        f"the project has not declared the admission:\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["reason"] == "project_incomplete", payload
    assert CONTRACT_FIELD in payload["error"], (
        f"[{label}] the refusal must name the field that caused it: {payload['error']!r}"
    )
    assert "contract_stale_admitted" not in payload, (
        f"[{label}] an undeclared run must not emit the #533 key at all -- an "
        f"emitted empty list would read as 'we checked and found none' on a "
        f"run that never checked: {payload!r}"
    )
    assert "CONTRACT-ONLY STALE ADMITTED" not in result.stderr, (
        f"[{label}] and must not print the disclosure block either"
    )


# ===========================================================================
# 3-4. Declared: the contract-only unit assembles, and says so by name.
# ===========================================================================


@pytest.mark.parametrize(
    "mismatched,label",
    [
        ((CONTRACT_FIELD,), "contract-only"),
        (("plugin_bundle_hash", CONTRACT_FIELD), "contract-plus-machinery"),
        ((CONTRACT_FIELD, CONTRACT_FIELD), "duplicated-member"),
        (tuple(MACHINERY_FIELDS) + (CONTRACT_FIELD,), "contract-plus-all-machinery"),
    ],
)
def test_declared_contract_stale_assembles_and_is_named(tmp_path, mismatched, label):
    """The post-upgrade shapes matter as much as the pure one: any plugin
    release moves plugin_bundle_hash on every converged unit, so a predicate
    that only accepted a singleton list would refuse the ordinary case.

    The `duplicated-member` row is not hypothetical hardening:
    ledger.schema.json gives stale_mismatched_fields minItems but NOT
    uniqueItems, so a hand-edited ledger can carry it, and a list-equality
    predicate would refuse here while final_audit.py's membership predicate
    admitted -- two gates disagreeing about one record.

    Mutation: compare the leftover fields as a sorted LIST rather than a set
    -> the duplicated-member row goes red."""
    root = one_contract_stale_book(tmp_path, admit=True, mismatched=mismatched)

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"[{label}] a declared contract-only stale unit must assemble:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["contract_stale_admitted"] == ["seg02"], payload
    assert payload["segments_assembled"] == 2, payload
    assert "CONTRACT-ONLY STALE ADMITTED (1)" in result.stderr, result.stderr
    assert "  ~ seg02" in result.stderr, result.stderr

    # ADMITTED, not merely un-refused: the prose has to reach the book.
    ns = json.loads(
        (root / "out" / ".assembled" / "nodestream.json").read_text(encoding="utf-8")
    )
    assert "p_seg02" in {n["id"] for n in ns["nodes"]}, (
        f"[{label}] seg02 was admitted but its content never reached the "
        f"assembled book"
    )


# ===========================================================================
# 5. Every content-affecting field still refuses, including an UNKNOWN one
# and a CASE-VARIED one.
# ===========================================================================


@pytest.mark.parametrize("field", CONTENT_AFFECTING_FIELDS + ["future_field_nobody_has_added_yet", "STYLE_CONTRACT_HASH"])
def test_declared_admission_still_refuses_any_other_moved_field(tmp_path, field):
    """The admission is for ONE field. Everything else -- the source text,
    the prompts, canon terms, engine/extraction config, an unrecognised
    future field, or the same name in the wrong case -- must still refuse,
    naming itself.

    The unknown and case-varied rows exist because an implementation that
    intersected against a KNOWN field list, or case-folded before comparing,
    would pass every row above them while shipping a book the tool is
    supposed to refuse.

    Mutation: replace the set-subset test with `CONTRACT_FIELD in unsafe` ->
    every row here goes red."""
    root = one_contract_stale_book(
        tmp_path, admit=True, mismatched=(field, CONTRACT_FIELD)
    )

    result = run_assemble(root)
    assert result.returncode == 2, (
        f"a stale unit carrying {field!r} alongside {CONTRACT_FIELD} must still "
        f"be refused:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["reason"] == "project_incomplete", payload
    assert field in payload["error"], (
        f"the refusal must name {field!r}, not merely refuse: {payload['error']!r}"
    )
    assert "contract_stale_admitted" not in payload, payload


# ===========================================================================
# 6. Malformed stale_mismatched_fields: the declaration changes nothing.
# ===========================================================================


@pytest.mark.parametrize(
    "mismatched,label",
    [
        (_UNSET, "key-missing"),
        ([], "empty-list"),
        ("style_contract_hash", "string-not-list"),
        ([{}], "unhashable-member"),
        ([1], "non-string-member"),
        ([None], "none-member"),
        ([CONTRACT_FIELD, 1], "mixed-members"),
    ],
)
def test_malformed_mismatched_fields_refuse_cleanly_even_when_declared(tmp_path, mismatched, label):
    """runs/ledger.json is never schema-validated by these readers, so every
    one of these shapes is reachable from a hand edit. Each must produce this
    script's own per-condition refusal (exit 2), never a TypeError escaping as
    an 'unexpected error' (exit 1) -- the declaration must not open a path
    around conditions 1 and 2.

    Mutation: move the #533 arm ahead of the str-members check -> the
    unhashable-member and mixed-members rows go red on exit code."""
    root = make_assemble_root(tmp_path, admit=True)
    write_book_scaffold(root, ["seg01", "seg02"])
    write_segment_draft(root, "seg01")
    write_segment_draft(root, "seg02")
    mark_sentinel_present(root, "seg02")
    kwargs = {} if mismatched is _UNSET else {"mismatched_fields": mismatched}
    write_ledger_segments(
        root,
        {
            "seg01": converged_ledger_record(root, "seg01"),
            "seg02": stale_ledger_record(root, "seg02", **kwargs),
        },
    )

    result = run_assemble(root)
    assert result.returncode == 2, (
        f"[{label}] a malformed stale_mismatched_fields must reach the "
        f"per-condition refusal, not an unexpected-error crash:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert payload["reason"] == "project_incomplete", payload
    assert "unexpected error" not in payload["error"], payload
    assert "contract_stale_admitted" not in payload, payload


# ===========================================================================
# 7. The sentinel, and its three states.
# ===========================================================================


def test_declared_admission_still_requires_a_sentinel(tmp_path):
    """The declaration says "the standard moved under work that WAS reviewed".
    A unit with no sentinel cannot be shown to have converged at all, so it is
    refused even when declared.

    Mutation: drop condition 4 for the contract arm -> red."""
    root = one_contract_stale_book(tmp_path, admit=True)
    (root / "segments" / ".ever_converged.seg02").unlink()

    result = run_assemble(root)
    assert result.returncode == 2, (
        f"a sentinel-less unit must be refused even when declared:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert "sentinel is absent" in payload["error"], payload
    # The refusal text must not call style_contract_hash machinery-only --
    # this script would then be asserting something false about its own
    # allowlist in its own diagnostic.
    assert "every moved field is machinery-only" not in payload["error"], (
        f"the sentinel refusal must characterise WHY the record got this far "
        f"correctly: {payload['error']!r}"
    )
    assert CONTRACT_FIELD in payload["error"], payload


def test_ambiguous_sentinel_admits_exactly_like_a_present_one(tmp_path):
    """Parity with the #491 machinery-only path, for the same reason: reading
    an unreadable dotfile as 'absent' would declare a finished book
    undeliverable, and unrecoverably so (the only route to a fresh sentinel is
    a retranslate, which select_segments.py's own Step 1 gate refuses for a
    segment that already converged)."""
    root = make_assemble_root(tmp_path, admit=True)
    write_book_scaffold(root, ["seg01", "seg02"])
    write_segment_draft(root, "seg01")
    write_segment_draft(root, "seg02")
    mark_sentinel_ambiguous(root, "seg02")
    write_ledger_segments(
        root,
        {
            "seg01": converged_ledger_record(root, "seg01"),
            "seg02": stale_ledger_record(root, "seg02", mismatched_fields=[CONTRACT_FIELD]),
        },
    )

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"an AMBIGUOUS sentinel must carve out exactly like a PRESENT one:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert parse_one_json_line(result)["contract_stale_admitted"] == ["seg02"]


# ===========================================================================
# 8. A hand-edited draft is still fatal -- the declaration admits a moved
# STANDARD, never moved PROSE.
# ===========================================================================


def test_declared_admission_never_covers_a_draft_edited_since_review(tmp_path):
    """The whole predicate rests on "no text changed, only the standard".
    assemble.py enforces that half through the sha1 comparison every accepted
    record already faces -- so this is exit 1 (a hard guard refusal), not the
    exit-2 completeness refusal, and the message names the real cause.

    Mutation: skip the sha1 comparison for contract-admitted records -> red."""
    root = one_contract_stale_book(tmp_path, admit=True)
    # Rewrite the draft AFTER the ledger recorded its reviewed sha1.
    write_segment_draft(root, "seg02", text="A hand edit the reviewer never saw.")

    result = run_assemble(root)
    assert result.returncode == 1, (
        f"a draft edited since review must not be assembled under the "
        f"declaration:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = parse_one_json_line(result)
    assert "draft has changed since review" in payload["error"], payload
    assert "contract_stale_admitted" not in payload, (
        f"a run that refused must not also report an admission: {payload!r}"
    )
    assert "CONTRACT-ONLY STALE ADMITTED" not in result.stderr, result.stderr
    assert not (root / "out" / ".assembled" / "nodestream.json").exists(), (
        "nothing may be written when the draft-trust guard fires"
    )


# ===========================================================================
# 9. True, but nothing qualifies: no new key, no new stderr block.
# ===========================================================================


def test_declaration_with_nothing_to_admit_changes_no_output(tmp_path):
    """A gate that announces itself on every run trains the reader to skip it,
    and an emitted empty list is a claim ('we checked, there were none') on a
    run where there was nothing to check.

    Mutation: emit the key unconditionally when declared -> red."""
    root = make_assemble_root(tmp_path, admit=True)
    write_book_scaffold(root, ["seg01"])
    write_segment_draft(root, "seg01")
    write_ledger_segments(root, {"seg01": converged_ledger_record(root, "seg01")})

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert "contract_stale_admitted" not in payload, payload
    assert "CONTRACT-ONLY STALE ADMITTED" not in result.stderr, result.stderr


# ===========================================================================
# 10. final_audit.py's pure predicate and arithmetic.
# ===========================================================================


def _classification(mismatched, stale_reason=("cache_key_mismatch",)):
    return {
        "seg01": {"category": "reusable"},
        "seg02": {
            "category": "stale",
            "stale_reason": list(stale_reason),
            "mismatched_fields": list(mismatched),
        },
    }


def _sentinels(state="present"):
    return {"seg01": (state, None), "seg02": (state, None)}


def test_final_audit_predicate_matches_assembles_verdict_row_for_row():
    """The two gates must agree about every shape, because W9 is gated on W7's
    own verdict: a record W7 admits and W9 refuses strands the book one step
    later than before, with a different diagnostic.

    Drives BOTH predicates over one table -- final_audit's on a
    classification entry, assemble's on a ledger record -- rather than
    asserting each against its own hand-written expectation, which is how two
    checks come to agree by construction instead of by behaviour."""
    fa = load_real_final_audit()
    asm = load_real_assemble()

    # (moved fields, admitted by #533 specifically, admitted by EITHER path).
    # The two columns differ exactly where the #491 machinery-only path owns
    # the record -- keeping them separate is the point: a change that routed
    # a machinery-only record through the #533 list would keep the second
    # column right and break the first.
    rows = [
        ([CONTRACT_FIELD], True, True),
        ([CONTRACT_FIELD, CONTRACT_FIELD], True, True),
        (["plugin_bundle_hash", CONTRACT_FIELD], True, True),
        (list(MACHINERY_FIELDS) + [CONTRACT_FIELD], True, True),
        (["plugin_bundle_hash"], False, True),   # the #491 path, not this one
        (list(MACHINERY_FIELDS), False, True),
        (["input_sha1", CONTRACT_FIELD], False, False),
        (["STYLE_CONTRACT_HASH", CONTRACT_FIELD], False, False),
        (["future_field", CONTRACT_FIELD], False, False),
        (["input_sha1"], False, False),
        ([], False, False),
    ]
    # Malformed MEMBER shapes are deliberately absent from this table and
    # covered against assemble.py alone (see the malformed-fields test above).
    # The two gates read structurally different inputs: assemble.py reads
    # runs/ledger.json's own stale_mismatched_fields, which nothing
    # schema-validates and a hand edit can fill with anything -- which is why
    # it carries an explicit str-members condition. final_audit.py reads
    # select_segments.py's DERIVED classification, whose mismatched_fields it
    # computes itself by diffing two cache_key dicts, so its members are
    # always field-name strings. Feeding [{}] to the #409 count raises
    # TypeError today (final_audit.py's count_stale_previously_converged has
    # no such condition); that is pre-existing, unreachable through any real
    # operational path, and #533 neither introduces nor relies on it. The new
    # #533 collector carries the str-members guard anyway, at no cost, so the
    # two predicates stay comparable shape-for-shape.
    for mismatched, by_contract, by_either in rows:
        cls, sentinels = _classification(mismatched), _sentinels()
        admitted = fa.collect_stale_contract_admitted(cls, sentinels)
        assert (admitted == ["seg02"]) is by_contract, (
            f"final_audit #533 path: {mismatched!r} -> {admitted!r}, expected "
            f"admitted={by_contract}"
        )
        fa_either = (
            fa.count_stale_previously_converged(cls, sentinels) + len(admitted)
        ) == 1
        assert fa_either is by_either, (
            f"final_audit either path: {mismatched!r} -> {fa_either}, expected "
            f"{by_either}"
        )

        record = {"stale_mismatched_fields": mismatched}
        # assemble's predicate covers BOTH acceptance paths, and reaches
        # condition 4 (the sentinel, a filesystem read) only once conditions
        # 1-3 have passed -- so "refused before the sentinel" is the
        # comparable signal, and it must line up with `by_either`, not with
        # the #533 column alone.
        reason = asm._stale_carveout_refusal_reason("seg02", record, True)
        refused_on_fields = reason is not None and "sentinel" not in reason
        assert refused_on_fields is not by_either, (
            f"assemble: {mismatched!r} -> {reason!r}, expected "
            f"admitted={by_either} -- the two gates disagree about this record"
        )


@pytest.mark.parametrize("mismatched", [[1], [{}], [None], [CONTRACT_FIELD, 1], "x", None, 5])
def test_final_audit_collector_never_raises_on_a_malformed_member(mismatched):
    """Unreachable through select_segments.py's own derived classification
    (see the note in the parity test above), and guarded regardless: this
    collector must return an empty list rather than raise, so that a future
    caller feeding it a raw ledger record cannot turn a fail-closed refusal
    into an uncaught TypeError.

    Mutation: drop the isinstance-str condition -> the [{}] row raises."""
    fa = load_real_final_audit()
    cls = {"seg02": {
        "category": "stale",
        "stale_reason": ["cache_key_mismatch"],
        "mismatched_fields": mismatched,
    }}
    assert fa.collect_stale_contract_admitted(cls, _sentinels()) == []


def test_final_audit_never_admits_a_draft_edited_since_review():
    """final_audit reads the draft-unchanged half off select_segments.py's own
    stale_reason rather than recomputing a sha1: draft_sha1_mismatch is
    present exactly when the on-disk draft no longer matches its recorded
    reviewed_draft_sha1.

    Mutation: test `'cache_key_mismatch' in stale_reason` instead of equality
    -> red."""
    fa = load_real_final_audit()
    for reason in (
        ["draft_sha1_mismatch"],
        ["draft_sha1_mismatch", "cache_key_mismatch"],
        [],
    ):
        admitted = fa.collect_stale_contract_admitted(
            _classification([CONTRACT_FIELD], stale_reason=reason), _sentinels()
        )
        assert admitted == [], f"stale_reason={reason!r} must never be admitted"


def test_final_audit_requires_the_sentinel_and_accepts_ambiguous():
    fa = load_real_final_audit()
    cls = _classification([CONTRACT_FIELD])
    assert fa.collect_stale_contract_admitted(cls, _sentinels("absent")) == []
    assert fa.collect_stale_contract_admitted(cls, _sentinels("present")) == ["seg02"]
    assert fa.collect_stale_contract_admitted(cls, _sentinels("ambiguous")) == ["seg02"]


def test_the_two_subtracted_populations_are_disjoint():
    """The #409 count and the #533 list must never claim the same segment: a
    double subtraction would make project_complete true on a book with a
    genuinely blocking stale unit left over.

    Not an argument -- driven over every combination of the four fields
    involved, asserting that no shape is counted twice and that the two
    together never exceed the raw stale count."""
    fa = load_real_final_audit()
    all_fields = list(MACHINERY_FIELDS) + [CONTRACT_FIELD]
    for r in range(1, len(all_fields) + 1):
        for combo in itertools.combinations(all_fields, r):
            cls = _classification(list(combo))
            sentinels = _sentinels()
            machinery = fa.count_stale_previously_converged(cls, sentinels)
            contract = fa.collect_stale_contract_admitted(cls, sentinels)
            assert machinery + len(contract) <= 1, (
                f"{combo!r} was subtracted by BOTH populations "
                f"(machinery={machinery}, contract={contract!r})"
            )
            assert machinery + len(contract) == 1, (
                f"{combo!r} was subtracted by NEITHER population -- every "
                f"combination of machinery fields and the contract field is "
                f"supposed to be admitted by exactly one of them"
            )


def test_project_complete_subtracts_both_and_stays_fail_closed():
    """Mutation: subtract only stale_previously_converged -> the two
    contract rows go red."""
    fa = load_real_final_audit()
    counts = {
        "not_started": 0,
        "recoverable": 0,
        "stale": 3,
        "blocked_needs_regeneration": 0,
        "human_escalation": 0,
    }
    assert fa.compute_project_complete(counts, 3) is True
    assert fa.compute_project_complete(counts, 1, 2) is True
    assert fa.compute_project_complete(counts, 0, 3) is True
    assert fa.compute_project_complete(counts, 1, 1) is False
    # The default keeps every pre-#533 caller on the old arithmetic exactly.
    assert fa.compute_project_complete(counts, 2) is False
    # A non-'stale' category still blocks regardless of either subtraction.
    blocked = dict(counts, not_started=1)
    assert fa.compute_project_complete(blocked, 0, 3) is False


# ===========================================================================
# 11. The declaration reader, and its three copies.
# ===========================================================================


@pytest.mark.parametrize(
    "profile,expected,label",
    [
        ({"validation": {"admit_contract_only_stale": True}}, True, "literal-true"),
        ({"validation": {"admit_contract_only_stale": False}}, False, "literal-false"),
        ({"validation": {"admit_contract_only_stale": None}}, False, "null"),
        ({"validation": {"admit_contract_only_stale": "true"}}, False, "string-true"),
        ({"validation": {"admit_contract_only_stale": 1}}, False, "integer-one"),
        ({"validation": {"untranslated_sentinel": "x"}}, False, "key-absent"),
        ({"validation": None}, False, "validation-null"),
        ({"validation": []}, False, "validation-not-a-mapping"),
        ({}, False, "block-absent"),
        (None, False, "no-profile-at-all"),
    ],
)
def test_the_declaration_reader_admits_only_a_literal_true(profile, expected, label):
    """`1 == True` in Python, so a truthiness check would read `1` as consent.
    The integer-one row is the one that catches that.

    All three shipped copies are driven over the SAME table -- a per-copy
    table would let one drift while its own row was updated to match."""
    readers = {
        "assemble": load_real_assemble().admit_contract_only_stale,
        "final_audit": load_real_final_audit().admit_contract_only_stale,
        "validate_assembled": load_real_validate_assembled().admit_contract_only_stale,
    }
    for name, reader in readers.items():
        assert reader(profile) is expected, (
            f"[{label}] {name}.admit_contract_only_stale({profile!r}) must be "
            f"{expected}"
        )


def test_validate_conservation_holds_no_fourth_copy_of_the_reader():
    """validate_conservation.py's population IS validate_assembled.py's, and
    it already imports that module. A local restatement there could drift
    against the very function whose argument it computes, and the symptom
    would be silent: blocks dropping out of the coverage lane."""
    source = VALIDATE_CONSERVATION_SRC.read_text(encoding="utf-8")
    assert "def admit_contract_only_stale" not in source, (
        "validate_conservation.py must call va.admit_contract_only_stale(), "
        "never define its own"
    )
    assert "va.admit_contract_only_stale(" in source, (
        "validate_conservation.py must pass the declaration through to the "
        "shared rebind helper -- without it the admitted segment silently "
        "drops out of the output-coverage population"
    )


def test_the_allowlist_itself_is_untouched_in_all_four_copies():
    """#533 must not widen SAFE_STALE_CARVEOUT_FIELDS. That set means 'can
    never change what the prose should say' -- false for the contract -- and
    it is read for two other questions besides this one."""
    asm = load_real_assemble()
    fa = load_real_final_audit()
    va = load_real_validate_assembled()
    ss = _load_module_from_source(
        SCRIPTS_SRC_DIR / "select_segments.py", "contract_admission__select_segments_ref"
    )
    expected = frozenset(MACHINERY_FIELDS)
    for name, value in (
        ("assemble", asm.SAFE_STALE_CARVEOUT_FIELDS),
        ("final_audit", fa.SAFE_STALE_CARVEOUT_FIELDS),
        ("validate_assembled", va.SAFE_STALE_CARVEOUT_FIELDS),
        ("select_segments", ss.MACHINERY_ONLY_CACHE_KEY_FIELDS),
    ):
        assert value == expected, (
            f"{name}'s machinery-only allowlist changed to {sorted(value)!r} -- "
            f"#533 is a separate acceptance path, never a widening of this set"
        )


# ===========================================================================
# Harness B -- validate_assembled.py (duplicated from
# tests/validate_assembled_carveout.test.py, plus the `admit` parameter)
# ===========================================================================


def make_va_root(tmp_path, admit=None, v1_scope="segment_drafts_and_audit") -> Path:
    root = tmp_path / "va_durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (VALIDATE_ASSEMBLED_SRC, VALIDATE_DRAFT_SRC):
        (scripts_dir / src.name).write_bytes(src.read_bytes())

    profile = {"output": {"v1_scope": v1_scope}}
    if admit is not None:
        profile["validation"] = {"admit_contract_only_stale": admit}
    (root / "profile.yml").write_text(yaml.safe_dump(profile), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )
    (root / "segments").mkdir()
    (root / "runs").mkdir()
    return root


def write_va_manifest(root: Path, blocks: dict, segments: list, heading_types=None) -> None:
    full_blocks = {}
    for bid, b in blocks.items():
        full = dict(b)
        full.setdefault("id", bid)
        full.setdefault("source_file", "source.txt")
        full.setdefault("sha1", "0" * 40)
        full_blocks[bid] = full
    manifest = {
        "blocks": full_blocks,
        "spine": [{"pos": 0, "file": "source.txt", "klass": "body"}],
        "segments": segments,
        "footnotes": [],
        "frontback": [],
        "verse": {"store": []},
        "source_inputs": ["source.txt"],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    if heading_types is not None:
        manifest["heading_types"] = heading_types
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def write_va_draft(root: Path, seg: str, blocks: dict) -> dict:
    draft = {"seg": seg, "blocks": blocks, "footnotes": {}, "verses": {}, "names": [], "notes": []}
    (root / "segments" / f"{seg}.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8"
    )
    return draft


def write_va_ledger(root: Path, entries: dict) -> None:
    segments = {}
    for seg, cfg in entries.items():
        record = {"status": cfg["status"]}
        draft_doc = json.loads(
            (root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8")
        )
        record["reviewed_draft_sha1"] = cfg.get(
            "reviewed_draft_sha1", draft_content_sha1_of(draft_doc)
        )
        if cfg["status"] == "stale" and "stale_mismatched_fields" in cfg:
            record["stale_mismatched_fields"] = cfg["stale_mismatched_fields"]
        segments[seg] = record
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8"
    )


def run_validate_assembled(root: Path, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "validate_assembled.py")],
        capture_output=True, text=True, timeout=timeout,
    )


def heading_bearing_contract_stale_root(tmp_path, admit=None) -> Path:
    """One segment that declares a heading and went stale on the contract
    alone. Without the admission its heading contributes 0 to the output
    side, and the structural gate HARD-fails."""
    root = make_va_root(tmp_path, admit)
    write_va_manifest(
        root,
        {"h1": {"type": "H1", "plain_text": "Chapter One", "order_index": 0}},
        [{"seg": "seg01", "kind": "body", "block_ids": ["h1"]}],
        heading_types=["H1"],
    )
    write_va_draft(root, "seg01", {"h1": "Глава первая"})
    write_va_ledger(
        root,
        {"seg01": {"status": "stale", "stale_mismatched_fields": [CONTRACT_FIELD]}},
    )
    return root


# ===========================================================================
# 12. The default-scope delivery gate, both ways round.
# ===========================================================================


@pytest.mark.parametrize("admit,label", [(None, "absent"), (False, "explicit-false")])
def test_validate_assembled_hard_fails_a_contract_stale_heading_when_undeclared(
    tmp_path, admit, label
):
    """Today this path is unreachable -- W7 refuses the book one gate earlier
    -- which is exactly why it has to be pinned before W7 starts admitting:
    the moment the completeness gate passes such a book, THIS gate is what it
    meets next. An explicit `false` must reach the identical outcome, defect
    list included, or "default-off" is only true of the absent case."""
    root = heading_bearing_contract_stale_root(tmp_path, admit)

    result = run_validate_assembled(root)
    assert result.returncode == 1, (
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert any(d["kind"] == "missing_heading" for d in payload["defects"]), payload
    assert "contract_stale_admitted" not in payload, payload


def test_validate_assembled_accepts_and_names_a_declared_contract_stale_heading(tmp_path):
    """Mutation: thread the flag into the predicate but not into the report ->
    the naming assertions go red while the exit code still passes, which is
    the shape of a gate that consumes an exception silently."""
    root = heading_bearing_contract_stale_root(tmp_path, admit=True)

    result = run_validate_assembled(root)
    assert result.returncode == 0, (
        f"a declared contract-only stale unit must satisfy the structural "
        f"gate:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["defects"] == [], payload
    assert payload["contract_stale_admitted"] == ["seg01"], payload
    assert "CONTRACT-ONLY STALE ADMITTED (1)" in result.stderr, result.stderr
    assert "  ~ seg01" in result.stderr, result.stderr


def test_validate_assembled_still_flags_a_declared_units_hand_edit(tmp_path):
    """The rebind is the point: admitting a unit into the population means it
    gets CHECKED, not trusted.

    And a unit the rebind REJECTS must not also be listed as admitted. That
    combination is not a cosmetic inconsistency -- it is one gate's output
    contradicting itself in two adjacent lines, naming a segment as trusted
    and as a HARD defect at once, and it is what an operator reads when
    deciding whether to ship.

    Mutation: record the admission at the carve-out branch instead of after a
    passing rebind -> the last assertion goes red while every other assertion
    in this file still passes."""
    root = heading_bearing_contract_stale_root(tmp_path, admit=True)
    write_va_draft(root, "seg01", {"h1": "A hand edit the reviewer never saw"})

    result = run_validate_assembled(root)
    assert result.returncode == 1, (
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert any(d["kind"] == "stale_review_since_audit" for d in payload["defects"]), payload
    assert "contract_stale_admitted" not in payload, (
        f"a unit that FAILED the reviewed-SHA rebind was never admitted by "
        f"this gate, so it must not appear in its admitted list: {payload!r}"
    )
    assert "CONTRACT-ONLY STALE ADMITTED" not in result.stderr, result.stderr


# ===========================================================================
# 13. The shared helper both default-scope gates get their population from.
#
#     Unit level, and deliberately not a validate_conservation.py test -- it
#     never loads that script. Section 14 below drives the real conservation
#     CLI over the same three declarations; this one pins the helper's own
#     3-tuple contract, so a population regression names the helper rather
#     than only reddening a CLI two layers up.
# ===========================================================================


@pytest.mark.parametrize("admit,eligible", [(None, False), (False, False), (True, True)])
def test_conservation_population_follows_the_declaration(tmp_path, admit, eligible):
    """validate_conservation.py's output-coverage lane is WARN-only, so a
    wrong population here never turns a gate red -- it silently reports on a
    different book. Driven through the real shared helper the script calls,
    with the real reader deciding the flag."""
    root = heading_bearing_contract_stale_root(tmp_path, admit)
    va = _load_module_from_source(
        root / "scripts" / "validate_assembled.py",
        f"contract_admission__va_copy_{id(root)}",
    )
    profile = yaml.safe_load((root / "profile.yml").read_text(encoding="utf-8"))
    ledger = json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

    trusted, _stale, contract_admitted = va.collect_reviewed_draft_rebind(
        ledger["segments"],
        va.collect_manifest_seg_ids(manifest["segments"]),
        va.admit_contract_only_stale(profile),
    )
    assert ("seg01" in trusted) is eligible, (
        f"admit={admit!r}: seg01 in the trusted population should be "
        f"{eligible} -- this set is what the coverage lane's eligible_keys is "
        f"built from"
    )
    assert (contract_admitted == ["seg01"]) is eligible, contract_admitted


# ===========================================================================
# 14. ...and the lane SAYS SO, through its own CLI.
#
#     Section 13 drives the shared helper and stops at the population it
#     returns. That leaves criterion 3 -- "every gate that admits a segment
#     NAMES it, on stderr and in its structured stdout" -- unpinned for this
#     one script: deleting validate_conservation.py's own
#     `contract_stale_admitted` emission and its `~ <seg>` stderr block keeps
#     section 13 green, because that helper is not where either lives. This
#     section runs the real CLI and reads both surfaces.
#
#     The fixture is the HOLLOWED shape tests/validate_conservation_carveout
#     .test.py established (real source words, empty draft block): it earns a
#     genuine `hollowed_output_block` WARN when the segment is eligible and
#     none at all when it is not, so "declared" and "undeclared" differ in the
#     lane's actual work as well as in its naming -- neither assertion can
#     pass vacuously against a lane that reports on nothing.
# ===========================================================================


def make_vc_root(tmp_path, admit=None) -> Path:
    """A default-scope root carrying one contract-stale segment whose single
    PARA block has real source content and an EMPTY draft."""
    root = make_va_root(tmp_path, admit)
    (root / "scripts" / VALIDATE_CONSERVATION_SRC.name).write_bytes(
        VALIDATE_CONSERVATION_SRC.read_bytes()
    )
    write_va_manifest(
        root,
        {
            "PARA:seg01:0001": {
                "type": "PARA",
                "plain_text": "This block has real, substantial source content.",
                "order_index": 0,
            }
        },
        [{"seg": "seg01", "kind": "body", "block_ids": ["PARA:seg01:0001"], "word_count": 7}],
    )
    write_va_draft(root, "seg01", {"PARA:seg01:0001": ""})
    write_va_ledger(
        root,
        {"seg01": {"status": "stale", "stale_mismatched_fields": [CONTRACT_FIELD]}},
    )
    return root


def run_validate_conservation(root: Path, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "validate_conservation.py"), "output-coverage"],
        capture_output=True, text=True, timeout=timeout,
    )


_HOLLOWED = ("seg01", "PARA:seg01:0001", "hollowed_output_block")


def test_conservation_cli_names_a_declared_contract_stale_segment(tmp_path):
    root = make_vc_root(tmp_path, admit=True)
    proc = run_validate_conservation(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    doc = parse_one_json_line(proc)

    kinds = {(w["seg"], w["block_id"], w["kind"]) for w in doc["warnings"]}
    assert _HOLLOWED in kinds, (
        "POSITIVE CONTROL failed -- with the declaration in force seg01 is in "
        "the eligible population, so its hollow block must earn a real WARN. "
        "Without this the naming assertions below would be about a lane doing "
        "no work\n" + proc.stdout + proc.stderr
    )
    assert doc.get("contract_stale_admitted") == ["seg01"], (
        "criterion 3: the lane must NAME the segment it only reported on "
        "because the operator declared it shippable\n" + proc.stdout
    )
    assert "CONTRACT-ONLY STALE ADMITTED (1)" in proc.stderr, proc.stderr
    assert "  ~ seg01" in proc.stderr, proc.stderr


@pytest.mark.parametrize("admit,label", [(None, "absent"), (False, "explicit-false")])
def test_conservation_cli_undeclared_neither_reports_nor_names(tmp_path, admit, label):
    root = make_vc_root(tmp_path, admit)
    proc = run_validate_conservation(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    doc = parse_one_json_line(proc)

    kinds = {(w["seg"], w["block_id"], w["kind"]) for w in doc["warnings"]}
    assert _HOLLOWED not in kinds, (
        f"admit={label}: seg01 is not in the eligible population, so its "
        f"hollow block must NOT be reported on\n" + proc.stdout + proc.stderr
    )
    assert "contract_stale_admitted" not in doc, (
        f"admit={label}: the key must be OMITTED, never emitted empty -- an "
        f"empty list reads as 'we checked and found none' on a run that never "
        f"checked\n" + proc.stdout
    )
    assert "CONTRACT-ONLY STALE ADMITTED" not in proc.stderr, proc.stderr
    assert "~ seg01" not in proc.stderr, proc.stderr
