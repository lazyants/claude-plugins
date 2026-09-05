"""tests/assemble_cli_args.test.py -- #861: W9's CLI surface.

`assemble.py` is the plugin's one WRITING deterministic step, and until this
release it parsed no arguments at all: no `argparse` import, no `sys.argv`
read. So `python3 assemble.py --help` -- the universal way to ask a CLI how it
is used -- ran a full book assembly and wrote the output vault, and
`--dry-run` assembled too. This suite pins the CLI boundary: argv is parsed,
and rejected, BEFORE `main()` does anything.

WHY THE FIXTURE HAS TO BE A REAL, ASSEMBLABLE BOOK. The obvious cheap bed --
a durable_root with no `profile.yml` -- makes this file's WRITE assertions
VACUOUS. Such a root halts at `reason: profile_precondition` before any write, so
"nothing was written" is true even for an implementation that parses argv
AFTER running `main()`. That exact ordering mutant

    if __name__ == "__main__":
        rc = main()
        _parse_cli_args()
        sys.exit(rc)

satisfies every WRITE assertion in the three assembly cases against a
profile-less root. (One of those three catches it anyway, incidentally rather
than by design: `main()` prints its `profile_precondition` JSON line before
returning, which the unknown-flag case's empty-stdout assertion trips over.
That is not the property this file exists to pin.) Only a root that genuinely
assembles makes the WRITE assertions capable of failing, which is why
`test_the_fixture_really_assembles` below is a MANDATORY CONTROL rather than a
nicety: if it goes red, the three assembly cases carry no evidence about argv
ordering at all, and its failure message says so. The two loader cases stage
their own file and do not depend on it.

Fixture helpers are RE-DERIVED here rather than imported from
`tests/assemble.test.py` or `tests/assemble_link_groups_wiring.test.py` --
this suite's convention is one self-contained file per module. The recipe is
the sibling's minimal converged one-segment book, trimmed to what W9 needs:
no `canon_link_groups.json` sidecar (so the loader is never imported at all),
the `## Mentions` appendix explicitly OFF, `output.target: obsidian`.

Two trimming details are load-bearing and easy to drop:
  - `output.adapter_config.obsidian.mentions_section.enabled` must be written
    EXPLICITLY false; omitting the key turns the appendix on, which pulls in
    `occurrence_targets.py` and its own dependencies.
  - `scripts/json_stdout.py` must be staged: every staged script loads it by
    exact path from beside itself, so a root without it exits before reaching
    anything this file is about.

The absence assertions compare a recursive listing of the root taken before
and after the run and require that no path was ADDED -- see
`assert_wrote_nothing()` for why creation, not mutation, is the whole signature
here. They additionally require the before-listing to be non-empty, which
catches an EMPTY root and nothing more; it is not a proof the root was fully
built. That proof is the control above plus the builder's own propagated
failures (`real_cache_key()` asserts the `cache_key.py` subprocess succeeded),
so a consistently incomplete builder reds the control and no green suite can
hide it.
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

CANON_LINK_GROUPS_SRC = SCRIPTS_SRC_DIR / "canon_link_groups.py"
# assemble.py's own runtime closure for this fixture's shape: the adapter
# resolver, the shipped Obsidian renderer, validate_draft.py (whose
# load_profile() assemble.py calls), cache_key.py (imported as a sibling at
# module import time), and json_stdout.py (loaded by exact path from beside
# each of them).
STAGED_SRCS = tuple(
    SCRIPTS_SRC_DIR / name
    for name in (
        "assemble.py",
        "output_resolve.py",
        "render_obsidian.py",
        "validate_draft.py",
        "cache_key.py",
        "json_stdout.py",
    )
)

for _src in (*STAGED_SRCS, CANON_LINK_GROUPS_SRC):
    assert _src.is_file(), f"fixture source not found: {_src}"

SOURCE_INPUT_NAME = "source.txt"
PARTICLE_CONFIG_NAME = "he_test.json"

NAME_SOURCE_FORM = "משה"
NAME_TARGET_FORM = "Moyshe"


# ---------------------------------------------------------------------------
# Fixture builders (re-derived; see this file's module docstring)
# ---------------------------------------------------------------------------

def _profile(root: Path) -> dict:
    return {
        "profile_version": 1,
        "project": {"title": "Test Book", "durable_root": str(root),
                    "pipeline_version": "v1", "max_segment_words": 15000},
        "source": {
            "format": "plain_text", "path": "/logical/source.txt", "gutenberg_id": None,
            "language": {"code": "he", "particle_config": PARTICLE_CONFIG_NAME,
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
            "target": "obsidian",
            "name_display": {"parenthetical_originals": "never"},
            "adapter_config": {
                # EXPLICITLY off -- omitting this key turns the appendix on and
                # drags occurrence_targets.py and its dependencies into the run.
                "obsidian": {"folders": {}, "mentions_section": {"enabled": False}},
                "epub": None, "custom": None,
            },
        },
    }


def _draft_content_sha1(doc: dict) -> str:
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(projected, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def _write_cache_key_inputs(root: Path, scripts_dir: Path) -> None:
    """The durable-root files cache_key.py's own field computers read.

    assemble.py recomputes every content-affecting cache-key field from the
    live root and refuses on a mismatch, so this fixture must carry real
    inputs and a real stored key. Only style_bible.md's two STYLE_CONTRACT
    markers are load-bearing; runs/.plugin_bundle_hash is the marker Step 0a
    writes and cache_key.py reads back rather than re-hashing the bundle.
    """
    def _fill(path: Path, data: bytes) -> None:
        # Every target below is guaranteed-absent in THIS fixture: STAGED_SRCS
        # carries neither bootstrap_names.py nor segpack.py, there is no
        # Mentions path pre-staging a languages/ config, and both directories
        # are created right here. The sibling this recipe comes from needed an
        # exists() guard for exactly those cases; this one would never take it.
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
        json.dumps({"PARTICLES": [], "STOPWORDS": [], "has_elision": False,
                    "ELISION_RE": None}).encode("utf-8"),
    )
    (root / "schemas").mkdir(exist_ok=True)
    for _name in ("draft.schema.json", "review.schema.json", "segpack.schema.json"):
        _fill(root / "schemas" / _name, b"{}\n")
    runs_dir = root / "runs"
    runs_dir.mkdir(exist_ok=True)
    _fill(runs_dir / ".plugin_bundle_hash", b"test-plugin-bundle-marker-v1\n")


def real_cache_key(root: Path, seg: str) -> dict:
    """The segment's REAL cache key, from the SHIPPED cache_key.py run against
    this fixture root -- never hand-typed, so it cannot drift from what
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


def make_root(tmp_path: Path) -> Path:
    """A minimal one-segment, one-block CONVERGED book that really assembles."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in STAGED_SRCS:
        shutil.copy2(src, scripts_dir / src.name)

    (root / "profile.yml").write_text(
        yaml.safe_dump(_profile(root), sort_keys=False), encoding="utf-8",
    )
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8",
    )
    (root / "canon.json").write_text(
        json.dumps(
            {
                "entries": {
                    NAME_SOURCE_FORM: {
                        "source_form": NAME_SOURCE_FORM, "is_proper_name": True,
                        "canonical_target_form": NAME_TARGET_FORM,
                        "basis": "transliterated", "confidence": "high",
                        "category": "person",
                    },
                },
                "review_queue": [],
                "generation_hashes": {"particle_config_hash": "x",
                                      "derivation_bundle_hash": "y"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    (root / "segments").mkdir()
    # runs/ is created by _write_cache_key_inputs, which writes into it.
    _write_cache_key_inputs(root, scripts_dir)

    manifest = {
        "blocks": {"p1": {"id": "p1", "type": "PARA", "seg": "seg01", "order_index": 0,
                          "plain_text": "source text",
                          "sha1": hashlib.sha1(b"p1").hexdigest(),
                          "source_file": SOURCE_INPUT_NAME}},
        "spine": [{"pos": 0, "file": SOURCE_INPUT_NAME, "klass": "body"}],
        "segments": [{"seg": "seg01", "kind": "body", "title_text": "Chapter One",
                      "block_ids": ["p1"], "word_count": 10}],
        "footnotes": [], "frontback": [], "verse": {"store": []},
        "source_inputs": [SOURCE_INPUT_NAME],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    segpack = {
        "seg": "seg01", "title": "seg01", "kind": "body", "word_count": 10,
        "blocks": [{"id": "p1", "order_index": 0, "plain_text": "source text"}],
        "footnotes": [], "verses": [], "names": [], "canon_names": [], "new_names": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y",
                              "particle_config_hash": "x", "derivation_bundle_hash": "y"},
    }
    (root / "segments" / "segpack_seg01.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8")

    draft = {"seg": "seg01", "blocks": {"p1": f"{NAME_TARGET_FORM} spoke."},
             "footnotes": {}, "verses": {}, "names": [], "notes": []}
    (root / "segments" / "seg01.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8")

    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": {"seg01": {
            "timestamp": "2026-01-01T00:00:00+00:00", "status": "converged", "rounds": 1,
            "cache_key": real_cache_key(root, "seg01"), "n_blocks": 1,
            "n_footnotes": 0, "n_verses": 0,
            "reviewed_draft_sha1": _draft_content_sha1(draft),
        }}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# Invocation + observation helpers
# ---------------------------------------------------------------------------

def run_assemble(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "assemble.py"), *args],
        capture_output=True, text=True, timeout=120,
    )


def listing(root: Path) -> set[str]:
    """The unordered set of every path under `root`, relative -- the before/
    after comparison the write-assertions use."""
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def assert_wrote_nothing(root: Path, before: set[str], proc, label: str) -> None:
    """No path was ADDED under `root`, and `out/` does not exist.

    Scoped deliberately: this detects creation, which is the whole of what an
    unasked-for assembly does to a fixture root (a vault under `out/` that was
    not there before). It does NOT detect a rewrite or a deletion of a path
    that already existed, and no broader snapshot machinery is warranted for
    a defect whose signature is creation.
    """
    assert before, (
        f"{label}: the fixture root listed EMPTY before the run, so this "
        f"assertion could not have failed. The fixture did not build."
    )
    after = listing(root)
    added = sorted(after - before)
    assert not added, (
        f"{label}: assemble.py wrote {len(added)} path(s) while being asked "
        f"{proc.args[2:]!r}:\n  " + "\n  ".join(added[:20])
        + f"\nstdout:\n{proc.stdout[:2000]}\nstderr:\n{proc.stderr[:2000]}"
    )
    assert not (root / "out").exists(), f"{label}: out/ exists after the run"


# ---------------------------------------------------------------------------
# MANDATORY CONTROL -- without this, every case below is vacuous
# ---------------------------------------------------------------------------

def test_the_fixture_really_assembles(tmp_path):
    """The bed must be a project a bare run DOES write. If this is red, the
    three assembly cases' "wrote nothing" assertions prove nothing about argv
    ordering: a root that cannot assemble writes nothing whatever the parser
    does. (The two loader cases below are independent of this fixture.)"""
    root = make_root(tmp_path)
    proc = run_assemble(root)
    assert proc.returncode == 0, (
        "CONTROL FAILED -- the fixture root does not assemble, so the three "
        "assembly cases in this file are VACUOUS.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one stdout JSON line, got {len(lines)}"
    result = json.loads(lines[0])
    assert result["success"] is True, result
    assert (root / "out" / ".assembled" / "nodestream.json").is_file()
    assert result["adapter_result"]["written"], (
        "CONTROL FAILED -- the adapter reported no written files, so the "
        "vault this issue is about was never produced."
    )


# ---------------------------------------------------------------------------
# The CLI boundary
# ---------------------------------------------------------------------------

def test_help_prints_usage_and_assembles_nothing(tmp_path):
    """#861's exact reproduction. Before the fix this exited 0 having written
    the whole vault; the operator's first instinct produced artifacts."""
    root = make_root(tmp_path)
    before = listing(root)
    proc = run_assemble(root, "--help")
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}"
    assert "usage:" in proc.stdout.lower(), proc.stdout
    assert "assemble.py" in proc.stdout
    assert_wrote_nothing(root, before, proc, "--help")


def test_an_unknown_flag_is_refused_and_assembles_nothing(tmp_path):
    """The other half of the same absence: with no parser, `--dry-run` was
    silently ignored and the book assembled anyway."""
    root = make_root(tmp_path)
    before = listing(root)
    proc = run_assemble(root, "--dry-run")
    assert proc.returncode == 2, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "--dry-run" in proc.stderr, proc.stderr
    assert not proc.stdout.strip(), (
        "a usage error must not emit anything on stdout -- that stream carries "
        f"this plugin's one-JSON-line verdict:\n{proc.stdout}"
    )
    assert_wrote_nothing(root, before, proc, "--dry-run")


def test_a_stray_positional_is_refused_and_assembles_nothing(tmp_path):
    root = make_root(tmp_path)
    before = listing(root)
    proc = run_assemble(root, "seg01")
    assert proc.returncode == 2, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert_wrote_nothing(root, before, proc, "stray positional")


# ---------------------------------------------------------------------------
# canon_link_groups.py -- a loader, and now it says so
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("args", [(), ("--help",)])
def test_the_link_groups_loader_says_it_is_not_a_cli(args):
    """It used to exit 0 printing nothing, for BOTH invocations -- silence that
    reads as "takes no arguments" rather than "is not a script".

    Runs the SHIPPED file in place rather than a staged copy: the guard raises
    before anything reads this module's `__file__`-derived path constants, and
    a script run as `__main__` is never byte-compiled, so the source tree is
    left untouched. That is also the invocation an operator actually makes."""
    proc = subprocess.run([sys.executable, str(CANON_LINK_GROUPS_SRC), *args],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert not proc.stdout.strip(), proc.stdout
    assert "not a command-line tool" in proc.stderr, proc.stderr
    assert "assemble.py" in proc.stderr, proc.stderr
