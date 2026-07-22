"""tests/_canon_project_fixture.py -- shared builder for an isolated,
Step-0a-shaped durable_root that the REAL canon/segpack scripts can be driven
against as subprocesses.

Extracted so the #290 bootstrap suite and the #291/#292 stamp-conservation
suite share ONE fixture rather than each carrying its own drifting copy. Not
a `*.test.py` file, so pytest never collects it (same convention as
`_senses_fixture.py`, which this module builds on rather than bypassing --
canon_senses.py staging still goes through the one sanctioned helper).

What makes this fixture worth sharing: it stages the REAL `cache_key.py`,
never the `FAKE_CACHE_KEY_PY` stub the sibling suites use. Every question
these two suites ask is about whether `canon.json`'s `generation_hashes` are
GENUINE and whether they move when they should -- a stub would make both
suites vacuous.

The source text is uncased Hebrew against the real shipped `he.json`, which
ships no `name_inventory`: `bootstrap_names.py`'s `Lu`-gated detector finds
zero candidates there by construction, which is exactly the zero-candidate
route #290 fixed and the state #291's restamp bypass is reachable from.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _senses_fixture import stage_consumer  # noqa: E402

PLUGIN_ROOT = TESTS_DIR.parent
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
SKILL_MD = SKILL_ROOT / "SKILL.md"
ASSETS_DIR = SKILL_ROOT / "assets"
SCRIPTS_SRC = ASSETS_DIR / "scripts"
SCHEMAS_SRC = ASSETS_DIR / "schemas"
LANGUAGES_SRC = ASSETS_DIR / "languages"

# Staged alongside canon_validate.py (which stage_consumer brings, together
# with canon_senses.py + canon-senses.schema.json).
STAGED_SCRIPTS = (
    "bootstrap_names.py",
    "cache_key.py",
    "glossary_batch_plan.py",
    "segpack.py",
)
CANON_SCHEMA_FILES = (
    "canon-entry.schema.json",
    "canon-batch.schema.json",
    "canon-file.schema.json",
)

PARTICLE_CONFIG = "he.json"

for _name in STAGED_SCRIPTS:
    assert (SCRIPTS_SRC / _name).is_file(), f"{_name} not found under {SCRIPTS_SRC}"
for _name in CANON_SCHEMA_FILES:
    assert (SCHEMAS_SRC / _name).is_file(), f"{_name} not found under {SCHEMAS_SRC}"
assert (LANGUAGES_SRC / PARTICLE_CONFIG).is_file(), f"{PARTICLE_CONFIG} not found under {LANGUAGES_SRC}"
assert SKILL_MD.is_file(), f"SKILL.md not found at {SKILL_MD}"


# Source-text fixture data (the book being translated), not code: an uncased
# Hebrew passage with no Lu-category letters anywhere.
HEBREW_BLOCK_ONE = "בראשית ברא אלוהים את השמים ואת הארץ ואת כל צבאם."
HEBREW_BLOCK_TWO = "והארץ הייתה תוהו ובוהו וחושך על פני תהום ורוח מרחפת."


def manifest_doc() -> dict:
    return {
        "segments": [
            {
                "seg": "seg01",
                "title_text": "פרק א",
                "kind": "body",
                "word_count": 18,
                "block_ids": ["p1", "p2"],
            }
        ],
        "blocks": {
            "p1": {"id": "p1", "seg": "seg01", "order_index": 0, "plain_text": HEBREW_BLOCK_ONE},
            "p2": {"id": "p2", "seg": "seg01", "order_index": 1, "plain_text": HEBREW_BLOCK_TWO},
        },
        "footnotes": [],
        "verse": {"store": []},
        "generation_hashes": {
            "source_extraction_hash": "a" * 40,
            "source_input_hash": "b" * 40,
        },
    }


def make_project(tmp_path) -> Path:
    """Stages an isolated durable_root the way Step 0a stages a real project:
    every script self-anchors off its own location, so all of them resolve
    canon.json / schemas/ / languages/ against THIS fixture rather than the
    repo's assets tree."""
    root = tmp_path / "durable_root"
    stage_consumer(root, "canon_validate.py")

    scripts_dir = root / "scripts"
    for name in STAGED_SCRIPTS:
        shutil.copy2(SCRIPTS_SRC / name, scripts_dir / name)

    schemas_dir = root / "schemas"
    for name in CANON_SCHEMA_FILES:
        shutil.copy2(SCHEMAS_SRC / name, schemas_dir / name)

    languages_dir = root / "languages"
    languages_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LANGUAGES_SRC / PARTICLE_CONFIG, languages_dir / PARTICLE_CONFIG)

    profile_path = root / "profile.yml"
    profile_path.write_text(
        yaml.safe_dump(
            {"source": {"language": {"particle_config": PARTICLE_CONFIG}}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )

    (root / "manifest.json").write_text(
        json.dumps(manifest_doc(), ensure_ascii=False), encoding="utf-8"
    )
    return root


def run_script(root: Path, name: str, *args, timeout=120):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / name), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(root),
    )


def run_canon_validate(root: Path, *args, research_mode="offline", timeout=120):
    return run_script(
        root, "canon_validate.py", "--research-mode", research_mode, *args, timeout=timeout
    )


def run_init(root: Path, research_mode="offline"):
    return run_canon_validate(root, "--init", research_mode=research_mode)


def run_segpack(root: Path):
    return run_script(
        root,
        "segpack.py",
        "--all",
        "--particle-config",
        PARTICLE_CONFIG,
        "--apparatus-policy",
        "omit_apparatus",
    )


def read_canon(root: Path) -> dict:
    return json.loads((root / "canon.json").read_text(encoding="utf-8"))


def stamp_of(root: Path) -> dict:
    return read_canon(root)["generation_hashes"]


def live_generation_hashes(root: Path) -> dict:
    """What cache_key.py computes for THIS project right now -- the values a
    real glossary merge would stamp."""
    values = {}
    for field in ("particle_config_hash", "derivation_bundle_hash"):
        proc = run_script(root, "cache_key.py", "--field", field)
        assert proc.returncode == 0, (
            f"cache_key.py --field {field} failed:\n{proc.stdout}\n{proc.stderr}"
        )
        values[field] = proc.stdout.strip()
        assert values[field], f"cache_key.py --field {field} printed an empty value"
    return values


def perturb_derivation_bundle(root: Path) -> None:
    """Edits a DERIVATION_BUNDLE_MEMBERS script so `derivation_bundle_hash`
    provably moves -- the real-world trigger for #193/#291 (a plugin upgrade
    that touches bootstrap_names.py or segpack.py)."""
    segpack_path = root / "scripts" / "segpack.py"
    segpack_path.write_bytes(segpack_path.read_bytes() + b"\n# fixture derivation-bundle edit\n")


def write_fragment(root: Path, items, name="fragment.json") -> Path:
    path = root / name
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return path


def accepted_item(source_form: str, target_form: str) -> dict:
    return {
        "source_form": source_form,
        "is_proper_name": True,
        "disposition": "accepted",
        "canonical_target_form": target_form,
        "basis": "transliterated",
        "confidence": "high",
    }


def queued_item(source_form: str, note: str = "disputed") -> dict:
    return {
        "source_form": source_form,
        "is_proper_name": True,
        "disposition": "review_queue",
        "note": note,
        "confidence": "low",
    }
