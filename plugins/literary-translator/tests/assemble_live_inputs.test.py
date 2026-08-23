"""tests/assemble_live_inputs.test.py -- #492: assembly must not ship a book
whose content-affecting cache-key inputs moved after the ledger snapshot it
reads was materialized.

## The defect

Every other gate in `assemble.py` reads `runs/ledger.json` -- the snapshot the
last `ledger_merge.py` run produced. That snapshot ages. An operator who edits
the STYLE_CONTRACT block of `style_bible.md` (a correct, deliberate,
R9-sanctioned edit that any consistency pass produces) and then runs assembly
WITHOUT re-running the merge got a book built from records that still said
`converged`, because nothing between the edit and the book recomputed anything.
The `reviewed_draft_sha1` guard does not see it either: the drafts genuinely did
not change; the standard they were reviewed against did. The pipeline normally
runs W7 before W9, so the intended flow never hit this -- but nothing ENFORCED
the ordering, and the failure was a green run, not a halt.

## What is pinned here

`assert_live_inputs_match_ledger()` re-derives the twelve content-affecting
cache-key fields from the live durable_root, using `cache_key.py`'s OWN field
computers, and compares them to each shipped record's stored `cache_key`. The
invariant, stated once and ONE-DIRECTIONAL on purpose: **a record the snapshot
still calls `converged` can no longer ship on a `ledger_merge.py` run that
predates the content edit.** The reverse is NOT pinned here and is not
implemented: a record the snapshot already calls `stale` is refused by
`assert_project_complete()` before this check runs, even where the live inputs
have since reverted to the reviewed key. That refusal is fail-closed and one
`ledger_merge.py` run from resolved; re-deciding a reverted key inside assembly
is the design #492's own body records as tried and rejected.

Both OUTCOMES are pinned -- refusing and assembling -- because a guard that only
ever refuses is as wrong as one that never does:

  - a moved content field REFUSES (`stale_live_inputs`), naming segment and
    field -- for a global input (style contract) and a per-segment one (canon
    terms) alike;
  - a moved MACHINERY field still ASSEMBLES -- the #491 carve-out population is
    excluded by construction, so a plugin upgrade cannot newly strand a book.
    That case rewrites `runs/.plugin_bundle_hash`, the marker Step 0a writes and
    `cache_key.py:563` reads back, and asserts the recomputed field really moved
    before asserting assembly survived it: editing a bundle script's bytes would
    leave the field unchanged and make the test vacuous;
  - the #533/R9 contract-only admission behaves identically on BOTH orderings,
    including its `.ever_converged` sentinel condition -- the sentinel matrix
    below is what distinguishes this implementation from one that admits a live
    style-contract drift unconditionally, which would ship a legacy
    pre-sentinel project without a merge and refuse it with one.

## Fixture strategy

Self-contained, per this suite's house convention -- the harness is duplicated
from tests/assemble.test.py's own `make_root()`/`write_ledger()` rather than
imported. Stored cache keys are computed by running the SHIPPED `cache_key.py`
against the fixture root (never hand-typed), so a fixture key cannot drift from
what `assemble.py` recomputes.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

ASSEMBLE_SRC = SCRIPTS_SRC_DIR / "assemble.py"
OUTPUT_RESOLVE_SRC = SCRIPTS_SRC_DIR / "output_resolve.py"
RENDER_OBSIDIAN_SRC = SCRIPTS_SRC_DIR / "render_obsidian.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
CACHE_KEY_SRC = SCRIPTS_SRC_DIR / "cache_key.py"

for _src in (ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC, VALIDATE_DRAFT_SRC,
             CACHE_KEY_SRC):
    assert _src.is_file(), f"fixture source not found: {_src}"

SOURCE_INPUT_NAME = "source.txt"
PARTICLE_CONFIG_NAME = "fr_test.json"
STYLE_CONTRACT_BODY = b"Formal register, Oxford comma.\n"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def default_profile(admit_contract_only_stale=None) -> dict:
    profile = {
        "profile_version": 1,
        "project": {
            "title": "Test Book",
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
                "particle_config": PARTICLE_CONFIG_NAME,
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
            "index": {"enabled": False, "person_grouping": False},
            "adapter_config": {
                "obsidian": {"folders": {}, "mentions_section": {"enabled": False}},
                "epub": None,
                "custom": None,
            },
        },
    }
    if admit_contract_only_stale is not None:
        profile["validation"]["admit_contract_only_stale"] = admit_contract_only_stale
    return profile


def _write_cache_key_inputs(root: Path, scripts_dir: Path) -> None:
    """The durable-root files cache_key.py's own field computers read. Only
    style_bible.md's two STYLE_CONTRACT markers are load-bearing for what this
    file tests; `runs/.plugin_bundle_hash` is the marker Step 0a writes and
    cache_key.py reads back rather than re-hashing the bundle."""
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
    write_style_bible(root, STYLE_CONTRACT_BODY)
    (root / "translate_TASK.md").write_bytes(b"TRANSLATE TASK PROMPT v1\n")
    (root / "review_TASK.md").write_bytes(b"REVIEW TASK PROMPT v1\n")
    (root / "extract.py").write_bytes(b"# extract.py fixture v1\n")
    (root / SOURCE_INPUT_NAME).write_bytes(b"Ceci est un texte source de test.\n")
    languages_dir = root / "languages"
    languages_dir.mkdir(exist_ok=True)
    (languages_dir / PARTICLE_CONFIG_NAME).write_text(
        json.dumps({"PARTICLES": ["de"], "STOPWORDS": ["le"], "has_elision": False,
                    "ELISION_RE": None}),
        encoding="utf-8",
    )
    (root / "schemas").mkdir(exist_ok=True)
    for name in ("draft.schema.json", "review.schema.json", "segpack.schema.json"):
        (root / "schemas" / name).write_bytes(b"{}\n")
    runs_dir = root / "runs"
    runs_dir.mkdir(exist_ok=True)
    (runs_dir / ".plugin_bundle_hash").write_text(
        "test-plugin-bundle-marker-v1\n", encoding="utf-8"
    )


def write_style_bible(root: Path, contract_body: bytes) -> None:
    """The two markers compute_style_contract_hash() requires, exactly once
    each. Only the bytes BETWEEN them are hashed, which is what makes "edit the
    contract" and "edit the surrounding prose" two different events."""
    (root / "style_bible.md").write_bytes(
        b"# Style Bible\n\nSome prose outside the contract.\n\n"
        b"<!-- STYLE_CONTRACT_BEGIN -->\n" + contract_body + b"<!-- STYLE_CONTRACT_END -->\n"
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


def make_root(tmp_path: Path, admit_contract_only_stale=None) -> Path:
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC, VALIDATE_DRAFT_SRC,
                CACHE_KEY_SRC):
        shutil.copy2(src, scripts_dir / src.name)

    profile = default_profile(admit_contract_only_stale)
    profile["project"]["durable_root"] = str(root)
    profile["output"]["destination"] = str(root / "out")
    (root / "profile.yml").write_text(
        yaml.safe_dump(profile, sort_keys=False), encoding="utf-8"
    )
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )
    (root / "canon.json").write_text(
        json.dumps(
            {"entries": {"Jean": {"target": "Жан", "kind": "person"}}, "review_queue": []},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "segments").mkdir()
    (root / "runs").mkdir()
    _write_cache_key_inputs(root, scripts_dir)
    return root


def write_manifest(root: Path, seg_ids) -> None:
    blocks = {}
    segments = []
    for seg in seg_ids:
        bid = f"p_{seg}"
        blocks[bid] = {
            "id": bid,
            "order_index": len(blocks),
            "type": "P",
            "sha1": hashlib.sha1(bid.encode()).hexdigest(),
            "source_file": SOURCE_INPUT_NAME,
        }
        segments.append({"seg": seg, "block_ids": [bid], "kind": "body"})
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "blocks": blocks,
                "spine": [{"pos": 0, "file": SOURCE_INPUT_NAME, "klass": "body"}],
                "segments": segments,
                "footnotes": [],
                "frontback": [],
                "verse": {"store": []},
                "source_inputs": [SOURCE_INPUT_NAME],
                "generation_hashes": {
                    "source_extraction_hash": "x",
                    "source_input_hash": "y",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def write_segment(root: Path, seg: str, text: str = "Le texte traduit.") -> dict:
    bid = f"p_{seg}"
    (root / "segments" / f"segpack_{seg}.json").write_text(
        json.dumps(
            {
                "seg": seg,
                "title": seg,
                "kind": "body",
                "word_count": 10,
                "blocks": [{"id": bid, "order_index": 0, "source_html": "<p>Le texte.</p>"}],
                "footnotes": [],
                "verses": [],
                "names": ["Jean"],
                # Referenced, so used_terms_hash actually projects a canon
                # entry: an unused entry legitimately moves nothing.
                "canon_names": ["Jean"],
                "new_names": [],
                "generation_hashes": {
                    "source_extraction_hash": "x",
                    "source_input_hash": "y",
                    "particle_config_hash": "x",
                    "derivation_bundle_hash": "y",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    draft = {
        "seg": seg,
        "blocks": {bid: text},
        "footnotes": {},
        "verses": {},
        "names": [],
        "notes": [],
    }
    (root / "segments" / f"{seg}.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8"
    )
    return draft


def draft_content_sha1_of(doc: dict) -> str:
    """The CONTENT hash ledger_update.py/assemble.py use, not a raw-bytes hash
    of the file. Duplicated rather than imported, per this suite's convention;
    tests/draft_sha1.test.py owns the exhaustively-tested original."""
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def mark_sentinel(root: Path, seg: str, kind: str) -> None:
    """`present` = a regular file; `ambiguous` = a dangling symlink (which
    carves out exactly like present, per classify_ever_converged_sentinel);
    `absent` = nothing at all."""
    path = root / "segments" / f".ever_converged.{seg}"
    if path.is_symlink() or path.exists():
        path.unlink()
    if kind == "present":
        path.write_bytes(b"converged\n")
    elif kind == "ambiguous":
        path.symlink_to(root / "segments" / "nothing-here-at-all")
    elif kind != "absent":
        raise AssertionError(f"unknown sentinel kind {kind!r}")


def write_ledger(root: Path, seg_ids, drafts) -> None:
    """A converged ledger whose stored cache keys are the REAL ones for this
    root. No knobs: the tests below start from a healthy book and then change
    exactly one thing on disk, and the two that need an unusual record (a
    bogus stored `cache_key`, a retained out-of-manifest entry) rewrite
    `ledger.json` explicitly rather than through a parameter here -- which
    keeps what each of them does visible at the test, and keeps this helper
    from advertising flexibility nothing uses."""
    segments = {}
    for seg in seg_ids:
        segments[seg] = {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "status": "converged",
            "rounds": 1,
            "cache_key": real_cache_key(root, seg),
            "n_blocks": 1,
            "n_footnotes": 0,
            "n_verses": 0,
            "reviewed_draft_sha1": draft_content_sha1_of(drafts[seg]),
        }
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8"
    )


def converged_book(tmp_path: Path, seg_ids=("seg01",), admit_contract_only_stale=None,
                   sentinel="present") -> "tuple[Path, dict]":
    """A complete, currently-assemblable project: manifest, segpacks, drafts,
    a ledger whose stored cache keys are the REAL ones for this root, and an
    `.ever_converged` sentinel per segment. Every test below starts here and
    then changes exactly one thing."""
    root = make_root(tmp_path, admit_contract_only_stale)
    write_manifest(root, seg_ids)
    drafts = {seg: write_segment(root, seg) for seg in seg_ids}
    for seg in seg_ids:
        mark_sentinel(root, seg, sentinel)
    write_ledger(root, seg_ids, drafts)
    return root, drafts


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


def load_assemble_module(root: Path, label: str):
    spec = importlib.util.spec_from_file_location(
        f"assemble_live_inputs_{label}", root / "scripts" / "assemble.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. The baseline: an untouched project still assembles, and the guard is
#    silent about it.
# ---------------------------------------------------------------------------


def test_untouched_project_assembles_and_says_nothing_new(tmp_path):
    root, _drafts = converged_book(tmp_path)
    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is True
    # No admission happened, so the #533 disclosure key must stay ABSENT --
    # a gate that announces itself on every run trains the reader to skip it.
    assert "contract_stale_admitted" not in payload, payload
    assert "stale_live_inputs" not in result.stderr


# ---------------------------------------------------------------------------
# 2. The issue's own sequence: a global content input edited after the merge.
# ---------------------------------------------------------------------------


def test_style_contract_edited_after_the_merge_refuses(tmp_path):
    """#492's exact reported sequence: every segment converged, the ledger was
    materialized, THEN the STYLE_CONTRACT block was edited, and assembly ran
    with no merge in between. Before this change the book assembled silently."""
    root, _drafts = converged_book(tmp_path)
    write_style_bible(root, b"Informal register, no Oxford comma. REVERSED.\n")

    result = run_assemble(root)
    assert result.returncode != 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["success"] is False
    assert payload.get("reason") == "stale_live_inputs", payload
    assert "style_contract_hash" in payload["error"], payload
    assert "seg01" in payload["error"], payload


def test_prose_outside_the_contract_block_is_not_a_content_edit(tmp_path):
    """The counterexample to the test above: only the bytes BETWEEN the two
    markers are hashed, so editing the surrounding prose must NOT refuse.
    Without this, the test above would pass just as well against a guard that
    fires on any style_bible.md write at all."""
    root, _drafts = converged_book(tmp_path)
    raw = (root / "style_bible.md").read_bytes()
    (root / "style_bible.md").write_bytes(
        raw.replace(b"Some prose outside the contract.", b"Completely different prose.")
    )

    result = run_assemble(root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


# ---------------------------------------------------------------------------
# 3. A PER-SEGMENT content input, so a global-only fix cannot pass.
# ---------------------------------------------------------------------------


def test_canon_edited_after_the_merge_refuses_on_a_per_segment_field(tmp_path):
    root, _drafts = converged_book(tmp_path)
    # A RE-DECISION on a name this segment actually uses -- the translator was
    # told "Жан" and the canon now says "Иоанн". An entry the segment never
    # references legitimately moves nothing (compute_used_terms_hash projects
    # only referenced names), so an addition would be the wrong edit to test.
    (root / "canon.json").write_text(
        json.dumps(
            {
                "entries": {"Jean": {"target": "Иоанн", "kind": "person"}},
                "review_queue": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_assemble(root)
    assert result.returncode != 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload.get("reason") == "stale_live_inputs", payload
    assert "used_terms_hash" in payload["error"], payload


# ---------------------------------------------------------------------------
# 4. The other side of the mutation: the machinery-only population must NOT
#    fire. #491's whole point is that a plugin upgrade cannot strand a book.
# ---------------------------------------------------------------------------


def test_machinery_only_drift_still_assembles(tmp_path):
    root, _drafts = converged_book(tmp_path)
    stored = json.loads((root / "runs" / "ledger.json").read_text())["segments"]["seg01"]

    # plugin_bundle_hash is NOT hashed from the bundle's bytes -- cache_key.py
    # reads it back from this marker, which Step 0a writes once per run. So
    # this is the only edit that actually moves the field; rewriting a bundle
    # script would leave it unchanged and make the assertion below vacuous.
    (root / "runs" / ".plugin_bundle_hash").write_text(
        "test-plugin-bundle-marker-v2-AFTER-UPGRADE\n", encoding="utf-8"
    )
    moved = real_cache_key(root, "seg01")
    assert moved["plugin_bundle_hash"] != stored["cache_key"]["plugin_bundle_hash"], (
        "the fixture edit did not actually move plugin_bundle_hash -- this test "
        "would be vacuous"
    )
    assert all(
        moved[f] == stored["cache_key"][f]
        for f in moved
        if f not in ("plugin_bundle_hash",)
    ), "the fixture edit moved more than the machinery field it targets"

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"a machinery-only live drift must never refuse -- that is the #491 "
        f"carve-out:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 5. #533/R9: the contract-only admission, and its sentinel condition, behave
#    the SAME on both orderings. This matrix is what separates this
#    implementation from one that admits a live contract drift unconditionally.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sentinel,expect_assembled", [
    ("present", True),
    ("ambiguous", True),
    ("absent", False),
])
def test_live_contract_only_admission_needs_the_same_sentinel_as_the_merged_path(
    tmp_path, sentinel, expect_assembled
):
    """A live style-contract-only drift under `admit_contract_only_stale` is
    admitted exactly when the merged path would admit the `stale` record it
    would have produced: sentinel PRESENT or AMBIGUOUS admits, ABSENT refuses.

    The absent row is the discriminator. A legacy project that converged before
    sentinels existed (see backfill_ever_converged.py) has `converged` records
    and no sentinel -- and would otherwise assemble here while being refused
    after a merge, which is the exact ordering-dependence #492 is about."""
    root, _drafts = converged_book(
        tmp_path, admit_contract_only_stale=True, sentinel=sentinel
    )
    write_style_bible(root, b"Formal register, Oxford comma. PLUS a new rule.\n")

    result = run_assemble(root)
    if expect_assembled:
        assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        payload = parse_one_json_line(result)
        assert payload["contract_stale_admitted"] == ["seg01"], payload
        assert "CONTRACT-ONLY STALE ADMITTED" in result.stderr
    else:
        assert result.returncode != 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        payload = parse_one_json_line(result)
        assert payload.get("reason") == "stale_live_inputs", payload
        assert "style_contract_hash" in payload["error"], payload


def test_live_contract_only_drift_without_the_declaration_still_refuses(tmp_path):
    """The declaration is the whole difference. Same drift, same sentinel, no
    `validation.admit_contract_only_stale` -- refuses."""
    root, _drafts = converged_book(tmp_path, admit_contract_only_stale=None)
    write_style_bible(root, b"Formal register, Oxford comma. PLUS a new rule.\n")

    result = run_assemble(root)
    assert result.returncode != 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert parse_one_json_line(result).get("reason") == "stale_live_inputs"


def test_the_declaration_does_not_admit_a_second_moved_field(tmp_path):
    """`admit_contract_only_stale` admits a contract-ONLY drift. A contract edit
    plus any other content move is not that, and must still refuse -- tested as
    a set, exactly as _stale_carveout_refusal_reason() tests its own."""
    root, _drafts = converged_book(tmp_path, admit_contract_only_stale=True)
    write_style_bible(root, b"Formal register, Oxford comma. PLUS a new rule.\n")
    (root / "translate_TASK.md").write_bytes(b"TRANSLATE TASK PROMPT v2\n")

    result = run_assemble(root)
    assert result.returncode != 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload.get("reason") == "stale_live_inputs", payload
    assert "prompt_hash" in payload["error"], payload


# ---------------------------------------------------------------------------
# 6. Fail-closed on an unusable stored key, and on an uncomputable live one.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bogus,label", [
    (None, "absent"),
    ("not-an-object", "string"),
    ([], "list"),
])
def test_a_record_without_a_usable_cache_key_refuses(tmp_path, bogus, label):
    """"Cannot confirm this record's inputs" must never read as "this record's
    inputs are unchanged"."""
    root, _drafts = converged_book(tmp_path)
    doc = json.loads((root / "runs" / "ledger.json").read_text())
    if bogus is None:
        del doc["segments"]["seg01"]["cache_key"]
    else:
        doc["segments"]["seg01"]["cache_key"] = bogus
    (root / "runs" / "ledger.json").write_text(json.dumps(doc), encoding="utf-8")

    result = run_assemble(root)
    assert result.returncode != 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload.get("reason") == "stale_live_inputs", payload
    assert "seg01" in payload["error"], payload


def test_an_uncomputable_live_input_refuses_rather_than_passing(tmp_path):
    """cache_key.py's own fail() raises SystemExit. Assembly must convert that
    into a refusal naming the segment, never let it escape as a traceback and
    never treat it as "nothing moved"."""
    root, _drafts = converged_book(tmp_path)
    (root / "style_bible.md").unlink()

    result = run_assemble(root)
    assert result.returncode != 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload.get("reason") == "stale_live_inputs", payload
    assert "seg01" in payload["error"], payload
    assert "Traceback" not in result.stderr


# ---------------------------------------------------------------------------
# 7. Scoping: a retained out-of-manifest entry must never newly block a book.
#    This is #491 round 2's invariant, restated for the new check.
# ---------------------------------------------------------------------------


def test_a_retained_out_of_manifest_entry_is_never_live_checked(tmp_path):
    """runs/ledger.json deliberately retains entries for segments the CURRENT
    manifest no longer contains. `segNN` here has no segpack at all, so any
    per-segment recompute over it would raise -- and would abort a book over a
    segment it does not even contain."""
    root, drafts = converged_book(tmp_path)
    doc = json.loads((root / "runs" / "ledger.json").read_text())
    doc["segments"]["seg99"] = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "status": "converged",
        "rounds": 1,
        "cache_key": {"input_sha1": "a" * 40},  # deliberately unusable
        "n_blocks": 1,
        "n_footnotes": 0,
        "n_verses": 0,
        "reviewed_draft_sha1": draft_content_sha1_of(drafts["seg01"]),
    }
    (root / "runs" / "ledger.json").write_text(json.dumps(doc), encoding="utf-8")
    # The entry has no draft on disk either, so it never reaches the check.
    (root / "segments" / "seg99.draft.json").write_text(
        json.dumps(drafts["seg01"], ensure_ascii=False), encoding="utf-8"
    )

    result = run_assemble(root)
    assert result.returncode == 0, (
        f"an out-of-manifest retained entry must not newly block an otherwise "
        f"assemblable book:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 8. The loop actually ran. A field loop or a segment loop that executes zero
#    times prints exactly what a passing one prints.
# ---------------------------------------------------------------------------


def test_every_shipped_segment_and_field_is_actually_compared(tmp_path):
    seg_ids = ("seg01", "seg02", "seg03")
    root, _drafts = converged_book(tmp_path, seg_ids=seg_ids)
    module = load_assemble_module(root, "counts")

    ledger = json.loads((root / "runs" / "ledger.json").read_text())
    manifest_ids = {s["seg"] for s in json.loads(
        (root / "manifest.json").read_text()
    )["segments"]}
    assert len(manifest_ids) >= 1, "vacuous fixture: no manifest segments"

    converged, _refusals, _admitted = module.load_converged_segments(
        ledger, manifest_ids, False
    )
    admitted, compared_pairs = module.assert_live_inputs_match_ledger(
        converged, manifest_ids, False
    )
    assert admitted == []
    assert compared_pairs == len(module.LIVE_CHECKED_CACHE_KEY_FIELDS) * len(manifest_ids), (
        f"expected every (segment, field) pair to be compared; got "
        f"{compared_pairs} for {len(manifest_ids)} segment(s) and "
        f"{len(module.LIVE_CHECKED_CACHE_KEY_FIELDS)} field(s)"
    )


def test_the_live_checked_field_set_is_the_carveout_complement(tmp_path):
    """Derived, never hand-listed: whatever cache_key.py declares MINUS the
    machinery-only allowlist. Pinning the relationship rather than a literal
    list is what makes a future 16th cache-key field live-checked by default --
    the fail-closed direction."""
    root, _drafts = converged_book(tmp_path)
    module = load_assemble_module(root, "fieldset")
    spec = importlib.util.spec_from_file_location(
        "assemble_live_inputs_ck", root / "scripts" / "cache_key.py"
    )
    ck = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ck
    spec.loader.exec_module(ck)

    assert set(module.LIVE_CHECKED_CACHE_KEY_FIELDS) == (
        set(ck.CACHE_KEY_FIELD_ORDER) - module.SAFE_STALE_CARVEOUT_FIELDS
    )
    assert set(module.LIVE_CHECKED_CACHE_KEY_FIELDS).isdisjoint(
        module.SAFE_STALE_CARVEOUT_FIELDS
    )
    assert len(module.LIVE_CHECKED_CACHE_KEY_FIELDS) == len(ck.CACHE_KEY_FIELD_ORDER) - 3
