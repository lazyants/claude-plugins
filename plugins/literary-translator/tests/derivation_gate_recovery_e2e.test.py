"""tests/derivation_gate_recovery_e2e.test.py -- the end-to-end proof for
#193/#291: a project blocked by the derivation-state gate can actually be
RECOVERED, using only the steps the gate's own message names.

Why this exists as its own suite. The pieces were already covered
individually -- `canon_stamp_conservation.test.py` proves the stamp rules,
`select_segments.test.py` proves the hint names the escape, and the
pre-existing `seg06_stale_regen_caughtup` case proves the gate clears once a
segpack has caught up. But nothing joined them, and the claim being made
(`Closes #193`) is that a brick which is LIVE in shipped releases is now
escapable. A closure resting on three separate half-proofs plus a manual
check rots the first time either half is refactored.

The obstacle this suite had to solve: `select_segments.test.py` drives its
gate with a FAKE `cache_key.py` stub (deterministic fixture keys), while
`canon_stamp_conservation.test.py` needs the REAL `cache_key.py` (the whole
point there is that the stamps are genuine). Neither fixture can prove
recovery on its own. So this suite stages one durable_root that satisfies
the REAL `cache_key.py --seg` computation AND runs the real
`canon_validate.py` / `segpack.py` / `select_segments.py` as subprocesses --
no stub anywhere in the chain.

The cycle under test, in the order an operator would perform it:

  1. converged project, segpack stamp current      -> reusable
  2. a plugin upgrade edits a DERIVATION_BUNDLE_MEMBERS script
                                                   -> blocked_needs_regeneration
  3. the pre-1.15.0 "escape" (an empty --merge-batches) no longer restamps
                                                   -> still blocked  (#291)
  4. canon_validate.py --restamp-derivation        -> canon stamp advances
  5. segpack.py re-run                             -> pack copies it forward
  6. select_segments.py                            -> gate CLEARED

Step 3 is the one that makes this more than a happy-path test: it pins that
#291 genuinely removed the old bypass, so step 4 is doing real work rather
than riding a side effect that would have cleared the gate anyway.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _canon_project_fixture import (  # noqa: E402
    PARTICLE_CONFIG,
    SCHEMAS_SRC,
    SCRIPTS_SRC,
    make_project,
    manifest_doc,
    perturb_derivation_bundle,
    read_canon,
    run_canon_validate,
    run_canon_init,
    run_script,
    run_segpack,
    stamp_of,
    write_fragment,
)

SEG = "seg01"


def _full_profile() -> dict:
    """Every profile key the REAL cache_key.py's 15-field `--seg` computation
    reads. Mirrors the shape canon_senses_bundle.test.py already proves
    sufficient, retargeted at this fixture's uncased-Hebrew source."""
    return {
        "project": {"pipeline_version": "v1.0.0"},
        "engine": {"effort": "medium", "max_fix_rounds": 3, "batch_agent_cap": 10},
        "source": {
            "format": "plain_text",
            "path": "/logical/original/path.txt",
            "language": {"code": "he", "particle_config": PARTICLE_CONFIG},
            "adapter_config": {"plain_text": {"encoding": "utf-8"}},
        },
        "target": {"language": {"code": "en"}},
        "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": 4},
        "footnotes": {"apparatus_policy": "omit_apparatus"},
        "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
    }


def make_pipeline_project(tmp_path) -> Path:
    """`make_project`'s canon-side root, extended with everything the real
    cache_key.py --seg and select_segments.py additionally need. Built here
    rather than in _canon_project_fixture so the two existing suites that
    share that helper are not perturbed by this suite's extra needs."""
    root = make_project(tmp_path)
    scripts_dir = root / "scripts"

    for name in ("select_segments.py", "ledger_merge.py"):
        shutil.copy2(SCRIPTS_SRC / name, scripts_dir / name)

    # The real schema set (cache_key hashes these; ledger_merge validates
    # fragments against them). Overlays the canon schemas already staged.
    shutil.copytree(SCHEMAS_SRC, root / "schemas", dirs_exist_ok=True)

    (root / "profile.yml").write_text(
        yaml.safe_dump(_full_profile(), sort_keys=False), encoding="utf-8"
    )

    begin, end = b"<!-- STYLE_CONTRACT_BEGIN -->", b"<!-- STYLE_CONTRACT_END -->"
    (root / "style_bible.md").write_bytes(
        b"# Style Bible\n\n" + begin + b"\n## A. Tone\nFormal.\n" + end + b"\n\n## G. Glossary\n"
    )
    (root / "translate_TASK.md").write_bytes(b"TRANSLATE v1\n")
    (root / "review_TASK.md").write_bytes(b"REVIEW v1\n")
    (root / "extract.py").write_bytes(b"# extract.py v1\n")

    source_file = root / "source_original.txt"
    source_file.write_bytes("ברא אלוהים את השמים.\n".encode("utf-8"))

    # One manifest serving BOTH consumers: segpack.py's segments/blocks view
    # and cache_key.py's source_inputs view.
    manifest = manifest_doc()
    manifest["source_inputs"] = [str(source_file.resolve())]
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "runs" / ".plugin_bundle_hash").write_text("baseline-marker\n", encoding="utf-8")
    return root


def current_cache_key(root: Path, seg: str = SEG) -> dict:
    proc = run_script(root, "cache_key.py", "--seg", seg)
    assert proc.returncode == 0, f"cache_key.py --seg {seg} failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


def write_draft(root: Path, seg: str, content: dict) -> str:
    """Canonical-JSON draft at the location select_segments.py's own
    `draft_path` resolves, returning its content sha1 -- the
    `reviewed_draft_sha1` a real converged fragment records."""
    raw = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    (root / "segments" / f"{seg}.draft.json").write_bytes(raw)
    return hashlib.sha1(raw).hexdigest()


def write_converged_fragment(root: Path, seg: str, cache_key: dict, draft_sha1: str) -> None:
    record = {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": 1,
        "cache_key": cache_key,
        "n_blocks": 2,
        "n_footnotes": 0,
        "n_verses": 0,
        "reviewed_draft_sha1": draft_sha1,
    }
    (root / "runs" / "ledger.d" / f"{seg}.json").write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


def classify(root: Path, seg: str = SEG) -> dict:
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "select_segments.py"), "--allow-empty"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(root),
    )
    assert proc.returncode == 0, f"select_segments.py failed:\n{proc.stdout}\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])["classification"][seg]


def test_blocked_derivation_gate_is_recoverable_via_the_documented_escape(tmp_path):
    root = make_pipeline_project(tmp_path)

    # ---- 1. A converged project whose segpack carries the current stamp ----
    assert run_canon_init(root).returncode == 0
    assert run_segpack(root).returncode == 0

    draft_sha1 = write_draft(root, SEG, {"text": "In the beginning."})
    write_converged_fragment(root, SEG, current_cache_key(root), draft_sha1)

    baseline = classify(root)
    assert baseline["category"] == "reusable", (
        f"fixture premise: the project must start converged/reusable, got {baseline}"
    )

    # ---- 2. A plugin upgrade edits a DERIVATION_BUNDLE_MEMBERS script ----
    stamp_before = stamp_of(root)
    perturb_derivation_bundle(root)

    blocked = classify(root)
    assert blocked["category"] == "blocked_needs_regeneration", (
        f"editing a derivation-bundle script must arm the gate, got {blocked}"
    )
    assert blocked["pending_fields"] == ["derivation_bundle_hash"]
    assert "--restamp-derivation" in blocked["message"]

    # ---- 3. #291: the pre-1.15.0 empty-merge bypass no longer clears it ----
    # Assertion ORDER is deliberate: the substantive checks come first, so
    # that against pre-#291 code this step fails by demonstrating the bypass
    # itself (the stamp moving, then the gate clearing) rather than by
    # tripping over the absence of the reporting field, which would be a much
    # weaker signal of what regressed.
    noop = run_canon_validate(root, "--merge-batches", str(write_fragment(root, [])))
    assert noop.returncode == 0, f"empty merge failed:\n{noop.stdout}\n{noop.stderr}"
    assert stamp_of(root) == stamp_before, (
        "#291 regression: a zero-item merge moved canon.json's "
        f"generation_hashes.\n  before: {stamp_before}\n  after:  {stamp_of(root)}"
    )
    assert run_segpack(root).returncode == 0
    assert classify(root)["category"] == "blocked_needs_regeneration", (
        "a content-free merge must NOT be able to clear the derivation-state "
        "gate -- that bypass is exactly what #291 removed"
    )
    assert json.loads(noop.stdout)["generation_hashes_restamped"] is False

    # ---- 4. The sanctioned escape the gate's own message names ----
    restamp = run_canon_validate(root, "--restamp-derivation")
    assert restamp.returncode == 0, f"--restamp-derivation failed:\n{restamp.stdout}\n{restamp.stderr}"
    assert json.loads(restamp.stdout)["generation_hashes_changed"] == ["derivation_bundle_hash"]
    assert stamp_of(root) != stamp_before

    # ---- 5. segpack.py copies the advanced stamp forward ----
    assert run_segpack(root).returncode == 0
    pack = json.loads((root / "segments" / f"segpack_{SEG}.json").read_text(encoding="utf-8"))
    assert pack["generation_hashes"]["derivation_bundle_hash"] == (
        read_canon(root)["generation_hashes"]["derivation_bundle_hash"]
    )

    # ---- 6. The gate clears ----
    cleared = classify(root)
    assert cleared["category"] != "blocked_needs_regeneration", (
        "#193 is NOT closed: after the documented restamp + segpack re-run the "
        f"segment is still blocked -- {cleared}"
    )
    assert cleared["category"] == "stale", (
        f"expected ordinary stale once the segpack caught up, got {cleared}"
    )
    assert cleared["mismatched_fields"] == ["derivation_bundle_hash"]
