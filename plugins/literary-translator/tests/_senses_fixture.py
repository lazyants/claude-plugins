"""tests/_senses_fixture.py -- the ONE sanctioned helper for staging
canon_senses.py (+ its schema) into a suite that isolates some OTHER
consumer script (canon_validate.py, glossary_batch_plan.py,
canon_adjudication_audit.py) -- RFC #215, plan §1a''/Cache section,
contract §7/§9.

Why this exists (R5-F4/R6-F1/R7-F1/R8-F2): any suite that copies/loads
ONLY a consumer script into an isolated durable_root breaks the instant
that consumer's module-level ``from canon_senses import ...`` runs --
``ModuleNotFoundError``, before any of the suite's own assertions fire.
Fixing this ad hoc, suite by suite, is exactly how the drift
``tests/senses_fixture_guard.test.py`` guards against creeps back in --
route every such staging through the functions below instead of a raw
``shutil.copy2``/``SourceFileLoader``/``spec_from_file_location`` of a
consumer script.

Two staging shapes exist, matching how a suite isolates its consumer:

  1. File-staging (subprocess invocation) -- ``stage_consumer(root, name)``:
     copies the REAL consumer script ``name`` (e.g. "canon_validate.py")
     plus canon_senses.py plus canon-senses.schema.json into an isolated
     ``durable_root``'s scripts//schemas dirs, mirroring exactly how
     Step 0a copies scripts/schemas in production. Used by every suite
     that ``subprocess.run``s its consumer against a ``tmp_path`` fixture
     root (``tests/canon_format_validation.test.py`` and siblings).
  2. In-process loading (``importlib``/``SourceFileLoader``) -- a consumer
     is loaded fresh, in-process, from its OWN REAL (non-copied) location.
     canon_senses.py, a real sibling file already on disk right next to
     it, resolves the moment the consumer's own directory
     (``SCRIPTS_SRC_DIR`` below) is on ``sys.path`` for the duration of
     the load -- exactly the ``sys.path.insert(0, str(SCRIPTS_DIR))`` /
     ``finally: sys.path.remove(...)`` idiom this plugin's suites already
     use for any sibling-importing script (not new to RFC #215). No
     separate helper function is exposed for this shape -- inserting
     ``SCRIPTS_SRC_DIR`` (this module's own constant, identical to every
     such suite's own ``SCRIPTS_DIR``) around the load is the whole fix.

canon_senses.py's own transitive deps are exactly stdlib + ``jsonschema``
(a real installed package -- see its own module docstring's "project-
dependency LEAF" discussion), so no further first-party file needs
staging alongside it today; if that ever changes, ``stage_consumer`` is
the one place to add it.
"""
import shutil
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
SCHEMAS_SRC_DIR = ASSETS_DIR / "schemas"

CANON_SENSES_SCRIPT = SCRIPTS_SRC_DIR / "canon_senses.py"
CANON_SENSES_SCHEMA = SCHEMAS_SRC_DIR / "canon-senses.schema.json"

assert CANON_SENSES_SCRIPT.is_file(), f"canon_senses.py not found at {CANON_SENSES_SCRIPT}"
assert CANON_SENSES_SCHEMA.is_file(), f"canon-senses.schema.json not found at {CANON_SENSES_SCHEMA}"


def stage_consumer(root: Path, name: str) -> Path:
    """Stages the REAL consumer script ``name`` (a bare filename under
    assets/scripts/, e.g. "canon_validate.py") plus canon_senses.py plus
    canon-senses.schema.json into an isolated ``root`` durable-root
    fixture, creating ``root/scripts/`` and ``root/schemas/`` if they
    don't already exist. Idempotent/overwrite-safe -- a suite that already
    created these dirs for its own OTHER fixture files (a fake
    cache_key.py stub, other schema files) can call this either before or
    after those. Returns the path the consumer script was staged to
    (``root/scripts/{name}``).
    """
    consumer_src = SCRIPTS_SRC_DIR / name
    assert consumer_src.is_file(), f"consumer script not found: {consumer_src}"

    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    schemas_dir.mkdir(parents=True, exist_ok=True)

    consumer_dst = scripts_dir / name
    shutil.copy2(consumer_src, consumer_dst)
    shutil.copy2(CANON_SENSES_SCRIPT, scripts_dir / "canon_senses.py")
    shutil.copy2(CANON_SENSES_SCHEMA, schemas_dir / "canon-senses.schema.json")
    return consumer_dst


# ---------------------------------------------------------------------------
# Authoritative fixture inventory (tests/senses_fixture_guard.test.py's OTHER
# sanctioned location for a raw consumer-isolation idiom): every entry here
# names a test file that isolates a canon_senses consumer WITHOUT calling
# stage_consumer() above, individually verified (see each entry's "note") to
# still make canon_senses importable at exec/run time. Adding an entry here
# is a deliberate, reviewed exception -- prefer stage_consumer() (or the
# plain sys.path.insert(SCRIPTS_SRC_DIR) idiom for an in-process load from a
# consumer's own real location) whenever the suite's own isolation shape
# allows it. Currently empty: every suite this guard has checked either
# calls stage_consumer() directly or already carries its own correct
# sys.path.insert(...)/canon_senses.py-staging -- both of which the guard
# recognizes on their own merits (see senses_fixture_guard.test.py's
# `_is_handled` for exactly what counts). Add here only for a shape the
# guard's own recognition genuinely cannot express.
# ---------------------------------------------------------------------------
AUTHORITATIVE_FIXTURE_INVENTORY = ()
