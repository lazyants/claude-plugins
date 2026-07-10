"""tests/ledger_e2e_acceptance.test.py -- THE mandatory, blocking release-gate
test for the full ledger / select_segments / cache-key resumability
subsystem (references/ledger-and-resumability.md, "A pilot/soak is
necessary but NOT sufficient alone" -- this is the fixture that paragraph
names: "build tests/ledger_e2e_acceptance.test.py FIRST"). Per the plan's
own non-goals section (§18 item 5), the plugin is not ship-ready until
THIS test (and a real second-project pilot, separately) actually run and
pass.

Unlike this repo's other ledger tests -- which each isolate ONE script
against a stubbed collaborator (ledger_merge.test.py stubs cache_key.py;
ledger_update.test.py drives ledger_update.py alone) -- this file wires
the REAL cache_key.py, ledger_update.py, ledger_merge.py, and
select_segments.py together against a single, realistic durable_root
fixture and drives them through two consecutive dispatch batches plus an
explicit human-escalation retry, IN ONE CONTINUOUS RUN, exactly the way a
real multi-batch project would accumulate ledger state over time. "Driven
by MOCKED agent outputs" means: the codex translate/review/fix loop
itself is mocked (this test writes draft/review artifacts and ledger
fragments directly, standing in for what a real agent turn would have
produced) -- but every SCRIPT invoked is the real, shipped one, run as a
real subprocess exactly as production invokes it.

The seven things this file proves, in order (see references/ledger-and-
resumability.md's authoritative enumeration):

  1. Batch 1 dispatches three segments: two converge, one exhausts
     engine.max_fix_rounds -> non_converged.
  2. A simulated interruption leaves a genuine (script-produced, not
     hand-authored) `in_progress` fragment for a fourth segment.
  3. style_bible.md -- a single project-level file, but the source of a
     GLOBAL cache_key field (style_contract_hash) -- is edited between
     batches, forcing the second classification pass to reclassify every
     previously-converged segment `stale`.
  4. Batch 2's select_segments.py correctly classifies: the interrupted
     segment as `recoverable` (dispatched exactly like `not_started`,
     UNAFFECTED by the style_bible.md edit, since an in_progress fragment
     never goes through the cache-key comparison at all), and the
     converged segments as `stale` (re-dispatched with a full-replace
     fragment -- none of the old, now-stale fragment's fields survive).
  5. `--only-segs <seg>` retries the human_escalation segment, re-entering
     SEGS as an explicit override and replacing its old terminal
     (non_converged) fragment with a fresh converged one.
  6. A final `ledger_merge.py --expected-segs` completeness check passes
     even though the fragment set now mixes a genuine batch-1-authored
     fragment (deliberately left untouched) with batch-2- and
     retry-authored ones.
  7. The final, MERGED ledger.json is asserted correct end-to-end: the
     untouched batch-1 fragment is materialized `stale` (its ON-DISK
     bytes are never rewritten -- "stale" only ever exists in the
     materialized ledger.json, never on a fragment on disk), while every
     re-dispatched segment is materialized `converged` and no longer
     flagged stale.

A note on the build spec's own segment-letter prose ("A/B/C" + "a 4th
segment"): item 1 names "A/B" as the two segments that converge in batch
1, but item 4 asserts "A recoverable ... B stale" in batch 2 -- which
cannot both be item 1's "A" (a segment that just converged cannot become
`recoverable`; only `stale` or `reusable` are reachable from `converged`
via a cache-key change). The only classification-report outcome that is
actually invariant to a global cache_key edit like this one is
`recoverable` (in_progress never touches the cache-key comparison at
all) -- so item 4's "A" is read here as item 2's unlettered "4th
segment", and "B" as one of item 1's two converging segments. This test
resolves the ambiguity by giving all four segments distinct, descriptive
names instead of overloading letters, and implements the scenario that is
actually reachable through the real scripts' own logic -- see the
per-segment comments below for the letter<->name mapping.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
SCHEMAS_SRC_DIR = ASSETS_DIR / "schemas"

CACHE_KEY_PY = SCRIPTS_SRC_DIR / "cache_key.py"
LEDGER_UPDATE_PY = SCRIPTS_SRC_DIR / "ledger_update.py"
LEDGER_MERGE_PY = SCRIPTS_SRC_DIR / "ledger_merge.py"
SELECT_SEGMENTS_PY = SCRIPTS_SRC_DIR / "select_segments.py"

for _p in (CACHE_KEY_PY, LEDGER_UPDATE_PY, LEDGER_MERGE_PY, SELECT_SEGMENTS_PY):
    assert _p.is_file(), f"required script not found at {_p}"
assert SCHEMAS_SRC_DIR.is_dir(), f"schemas dir not found at {SCHEMAS_SRC_DIR}"

# ---------------------------------------------------------------------------
# Segment naming vs. the build spec's letters (see the module docstring's
# note above for the ambiguity this resolves).
# ---------------------------------------------------------------------------

# Spec's "A" (one of the two batch-1 convergers): deliberately left
# un-redispatched after the style_bible.md edit, so a genuine batch-1-authored
# fragment survives, byte-for-byte untouched, all the way to the final merge
# (item 6: "fragments from BOTH batches") -- and so the final ledger.json
# assertion (item 7) can prove the materialized-only stale-flip end-to-end.
SEG_ALPHA = "seg_alpha"

# Spec's "B": converges in batch 1, reclassified `stale` by the same global
# style_bible.md edit, re-dispatched with a full-replace fragment in batch 2.
SEG_BETA = "seg_beta"

# Spec's "C": exhausts engine.max_fix_rounds in batch 1 -> non_converged
# (human_escalation); retried via --only-segs in item 5.
SEG_GAMMA = "seg_gamma"

# Spec's unlettered "a 4th segment": left `in_progress` by a simulated
# interruption in batch 1 (item 2); classified `recoverable` in batch 2
# regardless of the style_bible.md edit (item 4), since in_progress bypasses
# the cache-key comparison entirely.
SEG_DELTA = "seg_delta"

SEGMENTS = (SEG_ALPHA, SEG_BETA, SEG_GAMMA, SEG_DELTA)

BEGIN_MARKER = b"<!-- STYLE_CONTRACT_BEGIN -->"
END_MARKER = b"<!-- STYLE_CONTRACT_END -->"

ORIGINAL_STYLE_CONTRACT_INSIDE = (
    b"## A. Tone\nFormal register, vy-form throughout.\n"
    b"## F. Punctuation\nOxford comma required.\n"
)
# The between-batches edit (item 3) -- bytes strictly inside the markers
# change, which is exactly, and only, what style_contract_hash hashes.
EDITED_STYLE_CONTRACT_INSIDE = (
    b"## A. Tone\nFormal register, vy-form throughout -- REVISED mid-project "
    b"after an editorial note.\n"
    b"## F. Punctuation\nOxford comma required.\n"
)


def build_style_bible(inside: bytes) -> bytes:
    return (
        b"# Style Bible\n\nPreamble text outside the contract.\n\n"
        + BEGIN_MARKER
        + b"\n"
        + inside
        + END_MARKER
        + b"\n\n"
        + b"## G. Glossary\n\n- Jean -> \xd0\x96\xd0\xb0\xd0\xbd (locked form)\n"
    )


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def default_profile() -> dict:
    return {
        "project": {"pipeline_version": "v1.0.0"},
        "engine": {"effort": "high", "max_fix_rounds": 2, "batch_agent_cap": 1000},
        "source": {
            "format": "plain_text",
            "path": "/logical/original/source_path.txt",
            "language": {"code": "fr", "particle_config": "fr_particles.json"},
            "adapter_config": {
                "plain_text": {
                    "segmentation": {"method": "blank_line_run", "blank_line_threshold": 2}
                },
                "gutenberg_epub": {},
            },
        },
        "target": {"language": {"code": "ru"}},
        "verse_policy": {"mode": "literal_only", "threshold_lines": None},
        "footnotes": {"apparatus_policy": "translate_all"},
        "validation": {"untranslated_sentinel": "[NOT-TRANSLATED]"},
    }


def write_profile(root: Path, profile: dict) -> None:
    (root / "profile.yml").write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")


def default_segpack(seg: str) -> dict:
    return {
        "seg": seg,
        "blocks": [
            {"id": f"{seg}-b0", "order_index": 0, "plain_text": f"Bonjour depuis {seg}."},
            {"id": f"{seg}-b1", "order_index": 1, "plain_text": "Jean etait present ce jour-la."},
        ],
        "canon_names": ["Jean"],
        "new_names": [],
        "verses": [
            {"vid": f"{seg}-v1", "placeholder": f"⟦VERSE_{seg}_v1⟧", "parent_block": f"{seg}-b0"},
        ],
        "footnotes": [
            {"n": 1, "source_text": f"Une note de bas de page pour {seg}."},
        ],
    }


def write_segpack(root: Path, seg: str, segpack: dict) -> None:
    (root / "segments" / f"segpack_{seg}.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8"
    )


def make_durable_root(tmp_path) -> Path:
    """Builds a COMPLETE, internally-consistent durable_root fixture -- real
    copies of cache_key.py/ledger_update.py/ledger_merge.py/select_segments.py
    under scripts/ (so each script's own `Path(__file__).resolve().parents[1]`
    self-anchoring resolves against THIS isolated fixture, exactly like
    production), the real assets/schemas/*.schema.json tree (needed by
    ledger_merge.py's $ref-aware schema registry), and every file cache_key.py
    reads for its 15-field composite key: profile.yml, style_bible.md (with
    real STYLE_CONTRACT markers), instantiated prompt files, a particle_config
    file, extract.py, manifest.json + a source file, canon.json, and one
    segpack per segment.
    """
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (CACHE_KEY_PY, LEDGER_UPDATE_PY, LEDGER_MERGE_PY, SELECT_SEGMENTS_PY):
        shutil.copy2(src, scripts_dir / src.name)
    # derivation_bundle_hash's two members -- fixture content, never mutated
    # by this test (only style_contract_hash is exercised as the changing
    # field here; ledger_composite_key.test.py already regression-locks every
    # other field's own byte-scope in isolation).
    (scripts_dir / "bootstrap_names.py").write_bytes(b"# bootstrap_names.py fixture v1\n")
    (scripts_dir / "segpack.py").write_bytes(b"# segpack.py fixture v1\n")

    schemas_dir = root / "schemas"
    shutil.copytree(SCHEMAS_SRC_DIR, schemas_dir)

    write_profile(root, default_profile())
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}),
        encoding="utf-8",
    )

    (root / "style_bible.md").write_bytes(build_style_bible(ORIGINAL_STYLE_CONTRACT_INSIDE))
    (root / "translate_TASK.md").write_bytes(b"TRANSLATE TASK PROMPT v1\n")
    (root / "review_TASK.md").write_bytes(b"REVIEW TASK PROMPT v1\n")

    languages_dir = root / "languages"
    languages_dir.mkdir()
    (languages_dir / "fr_particles.json").write_bytes(b'{"particles": ["de", "du", "des"]}')

    (root / "extract.py").write_bytes(b"# extract.py fixture v1\n")

    source_file = root / "source_original.txt"
    source_file.write_bytes(
        b"Ceci est le texte source original pour ce projet fixture.\n"
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "segments": [{"seg": seg} for seg in SEGMENTS],
                "source_inputs": [str(source_file.resolve())],
            }
        ),
        encoding="utf-8",
    )

    (root / "canon.json").write_text(
        json.dumps({"entries": {"Jean": {"target": "Жан"}}, "review_queue": []}),
        encoding="utf-8",
    )

    (root / "segments").mkdir()
    for seg in SEGMENTS:
        write_segpack(root, seg, default_segpack(seg))

    runs_dir = root / "runs"
    runs_dir.mkdir()
    (runs_dir / ".plugin_bundle_hash").write_text(
        "baseline-plugin-bundle-hash-0000\n", encoding="utf-8"
    )

    return root


# ---------------------------------------------------------------------------
# Subprocess helpers -- every call below invokes the REAL, copied-into-the-
# fixture script exactly as production does.
# ---------------------------------------------------------------------------


def sha1_of_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def draft_content_sha1_of(doc: dict) -> str:
    """1.2.0: ledger_update.py/draft_sha1.py now hash a segment draft's
    CONTENT, not its raw on-disk bytes -- CONTRACT-1.2.0-reliability.md
    section 2. Independent, stdlib-only ground truth (drop 'dispatch_token'
    if present, sha1 the sorted-key canonical re-serialization); duplicated
    here rather than imported, matching this suite's "each test file stays
    self-contained" convention (see tests/draft_sha1.test.py's own
    canonical_expected_sha1() for the more-exhaustively-tested original)."""
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def _run(script_path: Path, args, root: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script_path), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(root),
    )


def run_cache_key(root: Path, seg: str) -> dict:
    proc = _run(root / "scripts" / "cache_key.py", ["--seg", seg], root)
    assert proc.returncode == 0, (
        f"cache_key.py --seg {seg} failed (rc={proc.returncode}):\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return json.loads(proc.stdout)


def run_select_segments(root: Path, extra_args=()):
    proc = _run(root / "scripts" / "select_segments.py", list(extra_args), root)
    assert proc.stdout.strip(), (
        f"select_segments.py produced no stdout (rc={proc.returncode}); stderr:\n{proc.stderr}"
    )
    return proc.returncode, json.loads(proc.stdout)


def run_ledger_merge(root: Path, extra_args=()):
    proc = _run(root / "scripts" / "ledger_merge.py", list(extra_args), root)
    assert proc.stdout.strip(), (
        f"ledger_merge.py produced no stdout (rc={proc.returncode}); stderr:\n{proc.stderr}"
    )
    return proc.returncode, json.loads(proc.stdout)


def write_payload(root: Path, tag: str, payload: dict) -> Path:
    path = root / "runs" / f".ledger_update_payload.{tag}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def run_ledger_update(root: Path, seg: str, payload: dict, tag: str) -> dict:
    payload_path = write_payload(root, tag, payload)
    proc = _run(
        root / "scripts" / "ledger_update.py",
        [seg, "--payload-file", str(payload_path)],
        root,
    )
    assert proc.stdout.strip(), (
        f"ledger_update.py {seg} produced no stdout (rc={proc.returncode}); stderr:\n{proc.stderr}"
    )
    stdout = json.loads(proc.stdout.strip())
    assert proc.returncode == 0 and stdout.get("success") is True, (
        f"ledger_update.py {seg} (tag={tag}) failed: rc={proc.returncode} "
        f"stdout={stdout} stderr={proc.stderr}"
    )
    # Mirrors recordLedgerPrompt's own mandated independent re-check -- never
    # trust the command's own fragment_sha1 claim without re-deriving it.
    assert sha1_of_bytes(Path(stdout["fragment_path"]).read_bytes()) == stdout["fragment_sha1"]
    return stdout


def read_fragment(root: Path, seg: str) -> dict:
    return json.loads((root / "runs" / "ledger.d" / f"{seg}.json").read_text(encoding="utf-8"))


def read_fragment_bytes(root: Path, seg: str) -> bytes:
    return (root / "runs" / "ledger.d" / f"{seg}.json").read_bytes()


def write_draft_and_review(root: Path, seg: str, text: str) -> str:
    """Writes a fresh draft + matching review artifact for `seg` -- standing
    in for the codex translate/review agents' real output files (the part of
    the engine loop this test mocks; only the LEDGER/RESUMABILITY MACHINERY
    is under test here). Returns the draft's own content-sha1 (1.2.0:
    ledger_update.py's draft_content_sha1() algorithm -- canonical JSON,
    dispatch_token excluded, NOT a raw-bytes hash), the value a real
    reviewer would stamp into segments/{seg}.review.json's draft_sha1 field.
    `text` is wrapped in a minimal valid draft.schema.json-shaped object
    (1.2.0 requires the draft file to be a JSON object) -- distinct `text`
    values still produce genuinely distinct content and therefore distinct
    hashes, so every caller's "different draft content -> different
    fragment/cache behavior" scenario is unaffected."""
    draft_path = root / "segments" / f"{seg}.draft.json"
    draft_doc = {"seg": seg, "blocks": {"p1": text}}
    draft_path.write_text(json.dumps(draft_doc, ensure_ascii=False), encoding="utf-8")
    draft_sha1 = draft_content_sha1_of(draft_doc)
    review_path = root / "segments" / f"{seg}.review.json"
    review_path.write_text(
        json.dumps({"draft_sha1": draft_sha1, "clean": True, "coverage_ok": True}),
        encoding="utf-8",
    )
    return draft_sha1


def converge_segment(
    root: Path, seg: str, rounds: int, tag: str, draft_text: str | None = None
) -> dict:
    """Simulates a segment reaching `converged` the way a real
    recordLedgerPrompt call would: writes a fresh draft+review pair, computes
    the CURRENT real 15-field cache_key (via the real cache_key.py -- exactly
    what select_segments.py/the real recordLedgerPrompt flow would compute at
    this moment), then invokes the real ledger_update.py to perform the
    actual atomic write, schema validation, and derived-field population.
    """
    if draft_text is None:
        draft_text = f"Translated prose for {seg}, round {rounds}.\n"
    write_draft_and_review(root, seg, draft_text)
    cache_key = run_cache_key(root, seg)
    payload = {"status": "converged", "rounds": rounds, "cache_key": cache_key}
    return run_ledger_update(root, seg, payload, tag)


def mark_in_progress(root: Path, seg: str, tag: str) -> dict:
    return run_ledger_update(root, seg, {"status": "in_progress"}, tag)


def mark_non_converged(root: Path, seg: str, reason: str, rounds: int, tag: str) -> dict:
    return run_ledger_update(
        root, seg, {"status": "non_converged", "reason": reason, "rounds": rounds}, tag
    )


# ---------------------------------------------------------------------------
# The mandatory 7-item, one-continuous-run acceptance test.
# ---------------------------------------------------------------------------


def test_ledger_e2e_acceptance_full_batch_cycle(tmp_path):
    root = make_durable_root(tmp_path)

    # -- Precondition (batch 1's own preflight): before any fragment exists,
    # every candidate is not_started, and SEGS is the full candidate list.
    rc, batch1_preflight = run_select_segments(root)
    assert rc == 0, batch1_preflight
    assert batch1_preflight["success"] is True
    assert batch1_preflight["segs"] == list(SEGMENTS)
    assert batch1_preflight["counts"] == {
        "reusable": 0,
        "stale": 0,
        "blocked_needs_regeneration": 0,
        "recoverable": 0,
        "not_started": 4,
        "human_escalation": 0,
    }
    for seg in SEGMENTS:
        assert batch1_preflight["classification"][seg] == {"category": "not_started"}

    # =========================================================================
    # ITEM (1): batch 1 dispatches seg_alpha/seg_beta/seg_gamma -- alpha/beta
    # converge, gamma exhausts engine.max_fix_rounds -> non_converged.
    # =========================================================================
    alpha_write_1 = converge_segment(root, SEG_ALPHA, rounds=1, tag="b1-alpha")
    beta_write_1 = converge_segment(root, SEG_BETA, rounds=1, tag="b1-beta")
    gamma_write_1 = mark_non_converged(
        root,
        SEG_GAMMA,
        reason="max_fix_rounds exhausted; final confirming review still not clean",
        rounds=2,
        tag="b1-gamma",
    )
    assert alpha_write_1["status"] == "converged"
    assert beta_write_1["status"] == "converged"
    assert gamma_write_1["status"] == "non_converged"

    alpha_fragment_batch1 = read_fragment(root, SEG_ALPHA)
    alpha_fragment_batch1_bytes = read_fragment_bytes(root, SEG_ALPHA)
    beta_fragment_batch1 = read_fragment(root, SEG_BETA)
    gamma_fragment_batch1 = read_fragment(root, SEG_GAMMA)
    assert gamma_fragment_batch1["reason"].startswith("max_fix_rounds")
    assert gamma_fragment_batch1["rounds"] == 2
    assert "cache_key" not in gamma_fragment_batch1

    # =========================================================================
    # ITEM (2): a simulated interruption -- a real `in_progress` ledger write
    # happens for seg_delta (exactly like a real recordLedgerPrompt('in_progress')
    # call mid-turn), then the batch process is interrupted before any
    # terminal (converged/non_converged/blocked) write ever happens for it.
    # =========================================================================
    delta_write_1 = mark_in_progress(root, SEG_DELTA, tag="b1-delta-interrupted")
    assert delta_write_1["status"] == "in_progress"
    delta_fragment_batch1 = read_fragment(root, SEG_DELTA)
    assert set(delta_fragment_batch1.keys()) == {"timestamp", "status"}

    # End of batch 1: mergeLedgerPrompt's own mandatory completeness gate.
    rc, merge1 = run_ledger_merge(root, ["--expected-segs", ",".join(SEGMENTS)])
    assert rc == 0, merge1
    assert merge1 == {
        "success": True,
        "ledger_path": str(root / "runs" / "ledger.json"),
        "n_segments": 4,
        "missing_segments": [],
        "stale_segments": [],
    }
    ledger_after_batch1 = json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))
    assert ledger_after_batch1["segments"][SEG_ALPHA]["status"] == "converged"
    assert ledger_after_batch1["segments"][SEG_BETA]["status"] == "converged"
    assert ledger_after_batch1["segments"][SEG_GAMMA]["status"] == "non_converged"
    assert ledger_after_batch1["segments"][SEG_DELTA]["status"] == "in_progress"

    # =========================================================================
    # ITEM (3): style_bible.md is edited between batches -- a real change
    # strictly inside the STYLE_CONTRACT markers, which is exactly what the
    # GLOBAL style_contract_hash cache_key field hashes. Both seg_alpha and
    # seg_beta (the two previously-converged segments) are affected equally
    # by a global field -- the build spec's prose only names "B" explicitly,
    # but real script logic makes this symmetric, so this test asserts both.
    # =========================================================================
    (root / "style_bible.md").write_bytes(build_style_bible(EDITED_STYLE_CONTRACT_INSIDE))

    # =========================================================================
    # ITEM (4): batch 2's select_segments.py classification pass.
    # =========================================================================
    rc, batch2_classification = run_select_segments(root)
    assert rc == 0, batch2_classification
    assert batch2_classification["success"] is True

    alpha_cls = batch2_classification["classification"][SEG_ALPHA]
    beta_cls = batch2_classification["classification"][SEG_BETA]
    gamma_cls = batch2_classification["classification"][SEG_GAMMA]
    delta_cls = batch2_classification["classification"][SEG_DELTA]

    # Only style_bible.md changed here, so cache_key_mismatch alone fires
    # (the draft itself is byte-for-byte untouched since batch 1).
    # select_segments.py's draft-sha1 recomputation now uses the same
    # draft_content_sha1() algorithm as ledger_update.py, so the two stay
    # consistent and draft_sha1_mismatch does not spuriously fire.
    assert alpha_cls == {
        "category": "stale",
        "stale_reason": ["cache_key_mismatch"],
        "mismatched_fields": ["style_contract_hash"],
    }
    assert beta_cls == {
        "category": "stale",
        "stale_reason": ["cache_key_mismatch"],
        "mismatched_fields": ["style_contract_hash"],
    }
    # seg_delta ("dispatched like not_started"): still recoverable, utterly
    # unaffected by the global style_bible.md edit -- an in_progress fragment
    # is classified purely by its non-terminal status, never touching the
    # cache-key comparison at all.
    assert delta_cls == {"category": "recoverable", "status": "in_progress"}
    # seg_gamma: unaffected, still human_escalation.
    assert gamma_cls == {
        "category": "human_escalation",
        "status": "non_converged",
        "reason": gamma_fragment_batch1["reason"],
    }

    assert batch2_classification["counts"] == {
        "reusable": 0,
        "stale": 2,
        "blocked_needs_regeneration": 0,
        "recoverable": 1,
        "not_started": 0,
        "human_escalation": 1,
    }
    assert batch2_classification["ids_by_category"]["stale"] == [SEG_ALPHA, SEG_BETA]
    assert batch2_classification["ids_by_category"]["recoverable"] == [SEG_DELTA]
    assert batch2_classification["ids_by_category"]["human_escalation"] == [SEG_GAMMA]

    # Emitted SEGS = not_started UNION recoverable UNION stale, in candidate
    # order -- seg_alpha/seg_beta are re-dispatched exactly like not_started;
    # seg_gamma is excluded pending an explicit override (item 5).
    assert batch2_classification["segs"] == [SEG_ALPHA, SEG_BETA, SEG_DELTA]
    assert batch2_classification["requested_only_segs"] is None
    assert batch2_classification["overrides"] == []
    assert batch2_classification["excluded_only_segs"] == []

    # =========================================================================
    # Batch 2 dispatch: this test deliberately drives only seg_beta and
    # seg_delta to completion this round -- a realistic partial batch, and
    # exactly the scenario the whole per-segment fragment design exists to
    # support. seg_alpha's ORIGINAL batch-1 fragment is left untouched on
    # disk, so a genuine batch-1-authored fragment survives into the final
    # merge alongside batch-2-/retry-authored ones (item 6).
    # =========================================================================
    beta_write_2 = converge_segment(
        root,
        SEG_BETA,
        rounds=2,
        tag="b2-beta-redispatch",
        draft_text="Translated prose for seg_beta, ROUND 2 (post style-bible edit).\n",
    )
    delta_write_2 = converge_segment(root, SEG_DELTA, rounds=1, tag="b2-delta-completed")
    assert beta_write_2["status"] == "converged"
    assert delta_write_2["status"] == "converged"

    beta_fragment_batch2 = read_fragment(root, SEG_BETA)
    delta_fragment_batch2 = read_fragment(root, SEG_DELTA)

    # Full-replace assertion (item 4): NONE of seg_beta's old (now-stale)
    # fragment values survive into the freshly re-converged fragment.
    # (A wall-clock `timestamp` inequality check was deliberately dropped
    # here -- it raced when both writes landed in the same second-resolution
    # clock tick. The four checks below already prove the full-replace
    # property from fragment *content*, per gotchas.md §13.)
    assert beta_fragment_batch1["rounds"] == 1
    assert beta_fragment_batch2["rounds"] == 2
    assert beta_fragment_batch2["cache_key"] != beta_fragment_batch1["cache_key"]
    assert (
        beta_fragment_batch2["cache_key"]["style_contract_hash"]
        != beta_fragment_batch1["cache_key"]["style_contract_hash"]
    )
    assert beta_fragment_batch2["reviewed_draft_sha1"] != beta_fragment_batch1["reviewed_draft_sha1"]
    # The new cache_key genuinely matches CURRENT truth (freshly recomputed
    # via the real cache_key.py) -- proving it is no longer stale.
    assert beta_fragment_batch2["cache_key"] == run_cache_key(root, SEG_BETA)

    # seg_delta: was in_progress with no cache_key/rounds at all; is now a
    # full converged record with everything ledger_update.py derives itself.
    for key in ("rounds", "cache_key", "n_blocks", "n_footnotes", "n_verses", "reviewed_draft_sha1"):
        assert key in delta_fragment_batch2
    assert delta_fragment_batch2["status"] == "converged"

    # =========================================================================
    # ITEM (5): --only-segs <seg_gamma> retries the human_escalation segment,
    # re-entering SEGS as an explicit, auditable override.
    # =========================================================================
    rc, retry_classification = run_select_segments(root, ["--only-segs", SEG_GAMMA])
    assert rc == 0, retry_classification
    assert retry_classification["success"] is True
    assert retry_classification["requested_only_segs"] == [SEG_GAMMA]
    assert retry_classification["segs"] == [SEG_GAMMA]
    assert retry_classification["overrides"] == [SEG_GAMMA]
    assert retry_classification["excluded_only_segs"] == []
    # The full classification report is unaffected by --only-segs -- gamma is
    # still genuinely human_escalation right up until this override fires.
    assert retry_classification["classification"][SEG_GAMMA]["category"] == "human_escalation"

    gamma_write_2 = converge_segment(root, SEG_GAMMA, rounds=1, tag="retry-gamma")
    assert gamma_write_2["status"] == "converged"
    gamma_fragment_batch2 = read_fragment(root, SEG_GAMMA)
    # The old terminal (non_converged) fragment's fields are fully replaced --
    # its `reason` does not survive the retry's converged write.
    assert "reason" not in gamma_fragment_batch2
    assert gamma_fragment_batch1["rounds"] == 2
    assert gamma_fragment_batch2["rounds"] == 1
    assert gamma_fragment_batch2["status"] == "converged"
    for key in ("cache_key", "n_blocks", "n_footnotes", "n_verses", "reviewed_draft_sha1"):
        assert key in gamma_fragment_batch2

    # =========================================================================
    # ITEM (6): final ledger_merge.py --expected-segs completeness check,
    # passing even though the fragment set now mixes a batch-1-authored
    # fragment (seg_alpha, deliberately untouched since), batch-2-authored
    # fragments (seg_beta, seg_delta), and a retry-authored one (seg_gamma).
    # =========================================================================
    rc, final_merge = run_ledger_merge(root, ["--expected-segs", ",".join(SEGMENTS)])
    assert rc == 0, final_merge
    assert final_merge["success"] is True
    assert final_merge["missing_segments"] == []
    assert final_merge["n_segments"] == 4
    assert final_merge["stale_segments"] == [SEG_ALPHA]

    # =========================================================================
    # ITEM (7): final assertion on the MERGED ledger.json's end-to-end
    # correctness.
    # =========================================================================
    final_ledger = json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))
    final_segments = final_ledger["segments"]
    assert set(final_segments.keys()) == set(SEGMENTS)

    # seg_alpha: materialized `stale` (its stored cache_key no longer matches
    # -- style_contract_hash has moved on) even though its ON-DISK fragment is
    # still literally the original batch-1 write, never rewritten -- "stale"
    # only ever exists in the materialized ledger.json, never on disk.
    assert final_segments[SEG_ALPHA]["status"] == "stale"
    assert read_fragment_bytes(root, SEG_ALPHA) == alpha_fragment_batch1_bytes
    on_disk_alpha = read_fragment(root, SEG_ALPHA)
    assert on_disk_alpha["status"] == "converged"
    assert on_disk_alpha["cache_key"] == alpha_fragment_batch1["cache_key"]

    # seg_beta/seg_gamma/seg_delta: all genuinely converged and current, no
    # longer flagged stale.
    for seg in (SEG_BETA, SEG_GAMMA, SEG_DELTA):
        assert final_segments[seg]["status"] == "converged"
    assert SEG_BETA not in final_merge["stale_segments"]
    assert SEG_GAMMA not in final_merge["stale_segments"]
    assert SEG_DELTA not in final_merge["stale_segments"]


# ---------------------------------------------------------------------------
# Regression-catcher: select_segments.py's own draft-sha1 recomputation must
# use the exact same draft_content_sha1() algorithm ledger_update.py writes
# `reviewed_draft_sha1` with -- never a raw-bytes hash of the file. This is
# the precise writer<->reader seam that regressed in 1.2.0: every converged
# segment above (batch 1 of the big acceptance test) would have been
# misclassified `stale` with `draft_sha1_mismatch` forever, purely because
# their on-disk draft bytes are never in canonical form. Pins the seam
# directly rather than relying on that indirectly.
# ---------------------------------------------------------------------------


def test_select_segments_reusable_survives_non_canonical_draft_bytes(tmp_path):
    """A converged segment whose on-disk draft is deliberately NON-canonical
    (top-level keys out of sorted order, pretty-printed with extra
    indentation, and a `dispatch_token` metadata field present) must still
    classify `reusable` -- proving select_segments.py's freshly recomputed
    draft-sha1 equals ledger_update.py's recorded `reviewed_draft_sha1`
    despite the on-disk bytes never matching the canonical serialization
    either algorithm re-derives its hash from. A regression back to a
    raw-bytes hash in select_segments.py would misclassify this `stale` with
    `draft_sha1_mismatch` even though nothing about the draft actually
    changed since review.
    """
    root = make_durable_root(tmp_path)
    seg = SEG_ALPHA

    # Deliberately non-canonical: keys in human-authored (non-alphabetical)
    # order, pretty-printed with indentation, and a `dispatch_token` metadata
    # field present -- exactly the shape a real on-disk draft has, never the
    # compact sorted-key form draft_content_sha1() re-serializes to.
    draft_doc = {
        "zzz_last_block": "content placed first on purpose",
        "dispatch_token": "some-run-token:seg_alpha",
        "seg": seg,
        "blocks": {"p1": "Translated prose for seg_alpha.\n"},
    }
    (root / "segments" / f"{seg}.draft.json").write_text(
        json.dumps(draft_doc, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # The hash a real reviewer (and ledger_update.py, independently) would
    # compute: canonical content hash, dispatch_token excluded -- deliberately
    # NOT the raw bytes of the pretty-printed file just written above.
    draft_sha1 = draft_content_sha1_of(draft_doc)
    (root / "segments" / f"{seg}.review.json").write_text(
        json.dumps({"draft_sha1": draft_sha1, "clean": True, "coverage_ok": True}),
        encoding="utf-8",
    )

    cache_key = run_cache_key(root, seg)
    write_result = run_ledger_update(
        root, seg, {"status": "converged", "rounds": 1, "cache_key": cache_key}, tag="noncanonical"
    )
    assert write_result["status"] == "converged"

    fragment = read_fragment(root, seg)
    # ledger_update.py computed this itself, independently, from the same
    # on-disk file -- sanity-check the fixture before trusting
    # select_segments.py's own separate recomputation below.
    assert fragment["reviewed_draft_sha1"] == draft_sha1

    # seg_beta/seg_gamma/seg_delta are untouched (not_started), so the
    # emitted SEGS is non-empty on its own -- no --allow-empty needed.
    rc, classification = run_select_segments(root)
    assert rc == 0, classification
    assert classification["classification"][seg] == {"category": "reusable"}


if __name__ == "__main__":
    import pytest as _pytest

    sys.exit(_pytest.main([__file__, "-v"]))
