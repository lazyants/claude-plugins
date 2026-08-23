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
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(CANON_SENSES_SCRIPT.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    shutil.copy2(CANON_SENSES_SCHEMA, schemas_dir / "canon-senses.schema.json")
    return consumer_dst


# ---------------------------------------------------------------------------
# Authoritative fixture inventory (tests/senses_fixture_guard.test.py's OTHER
# sanctioned location for a raw consumer-isolation idiom). Adding an entry
# here is a deliberate, reviewed exception -- prefer stage_consumer() (or the
# plain sys.path.insert(SCRIPTS_SRC_DIR) idiom for an in-process load from a
# consumer's own real location) whenever the suite's own isolation shape
# allows it. Add here only for a shape the guard's own recognition genuinely
# cannot express. Two such shapes exist:
#
#   1. A suite that really does isolate a consumer WITHOUT calling
#      stage_consumer(), individually verified (see the entry's "note") to
#      still make canon_senses importable at exec/run time. None today.
#   2. A suite that does not isolate a consumer AT ALL and merely NAMES one
#      inside an ordinary string literal, while separately using a copy/
#      loader idiom on unrelated scripts. The guard's check-1 gate is a
#      whole-file literal scan over code with docstrings and `#` comments
#      stripped -- but NOT ordinary string literals, because real isolation
#      code names its consumer exactly that way (`SCRIPTS_DIR /
#      "canon_validate.py"`). So a consumer name used as expected OUTPUT text
#      is indistinguishable from one used as a path component, file-locally.
#      Narrowing the scan to exclude string literals would blind the guard to
#      the real case, so the false positive is escaped here instead.
#
#      #361 added a THIRD way into shape 2, worth naming because it is not
#      "expected output text": a suite may name a consumer as a REAL path
#      component and still not isolate it, when it only READS that script's
#      SOURCE TEXT (`read_text()` + `ast.parse`) to recover one module-level
#      constant. No import, no exec, no copy -- so canon_senses is never
#      needed and nothing can fail at run time -- yet the path literal is
#      byte-identical to the isolation shape the scan is looking for. The
#      distinguishing fact is what the file does with the path, which is
#      exactly the file-local judgement the scan cannot make.
#
# Cost of a category-2 entry, stated plainly: the named file is skipped
# WHOLESALE, so a genuine unstaged isolation added to it later would not be
# preemptively flagged. That is the same tradeoff this guard already accepts
# for its documented loader-precision gap -- the miss fails LOUDLY
# (ModuleNotFoundError the instant the affected test runs), so a real
# regression cannot land silently; only preemptive naming is lost.
# ---------------------------------------------------------------------------
AUTHORITATIVE_FIXTURE_INVENTORY = (
    {
        "file": "fetch_citation.test.py",
        "category": 2,
        "note": (
            "Does not isolate any canon_senses consumer. Its ONE "
            "spec_from_file_location loads fetch_citation.py itself -- a "
            "stdlib-only script with no sibling imports at all, and not a "
            "consumer -- into `fc`, which is how this suite has always "
            "driven the retrieval boundary. It names glossary_batch_plan.py "
            "in exactly one place, added by #361: _default_batch_size() "
            "does read_text() + ast.parse on that script to recover the "
            "DEFAULT_BATCH_SIZE literal, so that the batch byte budget's "
            "stated derivation (1 MB of written evidence per source across "
            "the nominal default batch) cannot go stale in a comment while "
            "the batch size moves underneath it. The AST read is deliberate "
            "rather than an import: glossary_batch_plan.py does a bare "
            "`from canon_senses import ...` at module scope, so importing "
            "it would need sys.path surgery persisting for the session -- "
            "the very coupling this guard exists to police. Reading the "
            "source text needs no canon_senses at all, at parse time or "
            "run time. Verified 2026-08-23 by grepping every "
            "shutil.copy*/spec_from_file_location/SourceFileLoader call in "
            "the file: exactly one, naming fetch_citation.py."
        ),
    },
    {
        "file": "select_segments.test.py",
        "category": 2,
        "note": (
            "Does not isolate any canon_senses consumer: it copies only "
            "select_segments.py / ledger_merge.py / a stub cache_key.py into "
            "its fixture root. It names canon_validate.py solely inside the "
            "expected text of select_segments.py's own "
            "blocked_needs_regeneration hint, which since 1.15.0 (#193/#291) "
            "tells a blocked operator to run `canon_validate.py "
            "--restamp-derivation`. Verified 2026-07-22 by grepping every "
            "shutil.copy*/spec_from_file_location call in the file: none "
            "names a consumer script."
        ),
    },
    {
        "file": "canon_validate_plugin_root.test.py",
        "category": 2,
        "note": (
            "Does not isolate any canon_senses consumer either. Its ONE "
            "shutil.copy* copies cache_key.py -- a LEAF with no sibling "
            "imports of its own -- into a stand-in plugin install root, so "
            "the #412 --plugin-root override can be pointed at an untampered "
            "copy. The durable_root it drives is built by "
            "_canon_project_fixture.make_project(), which routes "
            "canon_validate.py through stage_consumer() and therefore stages "
            "canon_senses.py alongside it; this file never stages a consumer "
            "itself. It names canon_validate.py in CODE (not only in "
            "comments) solely inside two assertion-failure messages that tell "
            "a reader whose mode table drifted from whose. Verified "
            "2026-08-23 by reading every shutil.copy*/spec_from_file_location "
            "site in the file: the only copy target is cache_key.py, and the "
            "only in-process load goes through the shared "
            "_canon_project_fixture.load_canon_validate_module(), which uses "
            "the sanctioned sys.path.insert(SCRIPTS_SRC) idiom."
        ),
    },
)
