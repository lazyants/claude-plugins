#!/usr/bin/env python3
"""tests/name_discovery.test.py -- the caseless LLM name-discovery path (#286).

Follows this plugin's dominant subprocess pattern: the REAL shipped scripts are
copied into an isolated ``tmp_path/dr/scripts/`` so
``Path(__file__).resolve().parents[1]`` resolves against the fixture exactly as
it does in production, then run with ``sys.executable``, an explicit
``timeout=``, and ``capture_output=True``. Every assertion checks the exit code
AND the single stdout JSON line AND the stderr wording.

DISPATCH IS EXERCISED WITHOUT A LIVE MODEL. ``--node`` points at a shim that
plays codex-companion: it answers ``task`` by writing a canned reply into the
sandbox, answers ``status`` with a terminal record, and -- for the broker test
-- launches a distinct detached child whose argv matches the real teardown
selector and waits for that child to be visible to ``pgrep`` before returning.
No test ever calls a real model.

TWO FIXTURE PROPERTIES ARE ASSERTED BEFORE THEY ARE USED (``test_fixture_*``).
The corpus has to contain a form that is ALWAYS SUBSUMED by a longer inventory
form, and a non-empty block that no segment claims; a fixture that silently
stopped exercising either would make the census and partition tests pass
vacuously. So the fixture's own shape is checked first, and the substantive
tests depend on those checks having run.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
ASSETS = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC = ASSETS / "scripts"
SCHEMAS_SRC = ASSETS / "schemas"

SCRIPT = "name_discovery.py"

# Every script the staged copy self-anchors to or imports. Deliberately NOT
# glossary_batch_plan.py: it is a canon_senses consumer, and a copy of one
# staged without canon_senses.py beside it is exactly what
# tests/senses_fixture_guard.test.py exists to refuse. Nothing here loads it --
# the one test that names that file (d28) blob-compares it in its REAL location
# against origin/main and never touches the bed.
STAGED_SCRIPTS = (
    SCRIPT,
    "language_smoke_report.py",
    "bootstrap_names.py",
    "json_stdout.py",
    "review_artifact_check.py",
    "claim_record.py",
    "canon_link_groups.py",
    "resolve_codex_companion.py",
    "cache_key.py",
    "segpack.py",
)

TIMEOUT = 180

# ---------------------------------------------------------------------------
# The Hebrew corpus. Authored here, not in a data file, so the properties the
# tests depend on are visible next to the assertions that rely on them.
#
#   seg01 -- HEAD:seg01 carries NO `seg` field yet is listed in seg01.block_ids
#            (the real manifest shape: membership is block_ids, not `seg`).
#            Its text is "רבי נחמן" alone, so `נחמן` occurs in this book ONLY
#            where a longer inventory form covers it -- the subsumption case
#            extract_candidate_spans() would lose.
#   seg02 -- one real block plus a WHITESPACE-ONLY member block, which the
#            managed extraction gate accepts and which must not break coverage.
#   orphan -- FRONTBACK:fm01 is non-empty and claimed by NO segment.
# ---------------------------------------------------------------------------
RABBI = "רבי"                     # רבי
NACHMAN = "נחמן"             # נחמן
RABBI_NACHMAN = RABBI + " " + NACHMAN
MOSHE_LEIB = "משה לייב"   # משה לייב
MOSHE_LEIB_MAQAF = "משה־לייב"  # maqaf-joined
BRESLOV = "ברסלב"       # ברסלב
ELIYAHU = "אליהו"       # אליהו -- never in the corpus
BRESLOV_POINTED = "בְרַסלָב"  # pointed variant

BLOCKS = {
    # No `seg` field at all, yet a member of seg01 -- the real manifest shape.
    "HEAD:seg01": {"id": "HEAD:seg01", "type": "HEAD", "order_index": 0, "source_file": "book.xhtml",
                   "plain_text": RABBI_NACHMAN},
    "PARA:seg01:0001": {"id": "PARA:seg01:0001", "type": "PARA", "seg": "seg01",
                        "order_index": 1, "source_file": "book.xhtml",
                        "plain_text": RABBI_NACHMAN + " הלך."},
    "PARA:seg02:0001": {"id": "PARA:seg02:0001", "type": "PARA", "seg": "seg02",
                        "order_index": 2, "source_file": "book.xhtml",
                        "plain_text": MOSHE_LEIB_MAQAF + " בא."},
    # Whitespace-only, and a legitimate segment member.
    "WS:seg02:0002": {"id": "WS:seg02:0002", "type": "PARA", "seg": "seg02",
                      "order_index": 3, "source_file": "book.xhtml", "plain_text": "   "},
    # Non-empty and claimed by no segment.
    "FRONTBACK:fm01": {"id": "FRONTBACK:fm01", "type": "FRONTBACK", "seg": None,
                       "order_index": 4, "source_file": "book.xhtml",
                       "plain_text": BRESLOV_POINTED + " היא עיר."},
}
# segments is an ARRAY of records, each carrying its own `seg` and an ordered
# `block_ids` -- manifest.schema.json's shape, and what extract.py.template
# writes. A dict-shaped fixture here would be a lie that hides a production
# fatal, so test_fixture_manifest_matches_the_production_schema below
# schema-validates this against the real manifest.schema.json.
SEGMENTS = [
    {"seg": "seg01", "kind": "body", "word_count": 4,
     "block_ids": ["HEAD:seg01", "PARA:seg01:0001"]},
    {"seg": "seg02", "kind": "body", "word_count": 3,
     "block_ids": ["PARA:seg02:0001", "WS:seg02:0002"]},
]

# What the model "returns" per unit in the default fixture. Note MOSHE_LEIB is
# space-joined while the source spells it maqaf-joined (the #238/#241 fold must
# still match it), BRESLOV is unpointed while the source is pointed, and
# ELIYAHU occurs nowhere (the occurrence filter must drop it).
REPLY_FORMS = [RABBI_NACHMAN, NACHMAN, MOSHE_LEIB, BRESLOV, ELIYAHU]

EXPECTED_SURVIVORS = {RABBI_NACHMAN, NACHMAN, MOSHE_LEIB, BRESLOV}


def manifest_doc():
    """The fixture manifest, with each block's `sha1` derived rather than
    invented -- the real schema requires it, and a hand-typed constant would
    drift the moment a fixture's text changed."""
    blocks = {}
    for bid, block in BLOCKS.items():
        rec = dict(block)
        rec["sha1"] = hashlib.sha1(
            rec["plain_text"].encode("utf-8")).hexdigest()
        blocks[bid] = rec
    # Every top-level key manifest.schema.json requires, so the fixture can be
    # validated against the REAL schema. The six this feature never reads are
    # present at their minimum legal value; a subset schema would have let an
    # invalid fixture through, which is the same failure that hid a production
    # fatal behind a dict-shaped `segments`.
    return {
        "blocks": blocks,
        "segments": [dict(r) for r in SEGMENTS],
        "spine": [],
        "footnotes": [],
        "frontback": [],
        "verse": {"store": []},
        "source_inputs": ["source/book.txt"],
        "generation_hashes": {
            "source_extraction_hash": "0" * 40,
            "source_input_hash": "0" * 40,
        },
    }


def he_config(name_inventory=None):
    doc = {"PARTICLES": [], "STOPWORDS": ["של"],
           "has_elision": False, "ELISION_RE": None}
    if name_inventory is not None:
        doc["name_inventory"] = list(name_inventory)
    return doc


# ---------------------------------------------------------------------------
# Fixture staging
# ---------------------------------------------------------------------------

@pytest.fixture
def bed(tmp_path, monkeypatch):
    """A staged durable root OUTSIDE every implicit codex write root, with
    TMPDIR pointed at a directory of its own.

    WHERE THE BED LIVES MATTERS, and redirecting TMPDIR alone does not settle
    it: the driver refuses a durable_root under /tmp or $TMPDIR because codex
    makes those writable whatever --cwd it is handed, and `/tmp` is an implicit
    root UNCONDITIONALLY -- pointing TMPDIR elsewhere does not move a bed that
    is already under /tmp. On Linux pytest's tmp_path is exactly that, while on
    macOS it is under /var/folders, so a bed rooted at tmp_path passes on this
    machine and is refused on CI. The durable root therefore goes under $HOME,
    which is the remedy the #806 glossary-driver suite already uses, and which
    is what a real operator's machine looks like anyway. The sandboxes stay
    under tmp_path, where they belong and where a test can still see them.
    """
    home_base = Path(tempfile.mkdtemp(prefix="lt286-bed-", dir=str(Path.home())))
    root = tmp_path / "bed"
    root.mkdir()
    dr = home_base / "dr"
    (dr / "scripts").mkdir(parents=True)
    (dr / "languages").mkdir()
    (dr / "schemas").mkdir()
    for name in STAGED_SCRIPTS:
        src = SCRIPTS_SRC / name
        assert src.is_file(), f"shipped script missing: {src}"
        shutil.copy2(src, dr / "scripts" / name)
    for schema in SCHEMAS_SRC.glob("*.json"):
        shutil.copy2(schema, dr / "schemas" / schema.name)

    (dr / "manifest.json").write_text(
        json.dumps(manifest_doc(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (dr / "languages" / "he.local.json").write_text(
        json.dumps(he_config(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(ASSETS / "languages" / "he.json", dr / "languages" / "he.json")

    tmpdir = root / "tmp"
    tmpdir.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmpdir))
    yield dr
    # tmp_path is pytest's to reap; this one is ours.
    shutil.rmtree(home_base, ignore_errors=True)


def run(bed_dr, *argv, expect=None, env=None):
    """Run the staged script and return (returncode, parsed-stdout-or-None, stderr)."""
    cmd = [sys.executable, str(bed_dr / "scripts" / SCRIPT)] + list(argv)
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT,
                          env=full_env)
    out = (proc.stdout or "").strip()
    parsed = None
    if out:
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert len(lines) == 1, (
            f"expected exactly one JSON line on stdout, got {len(lines)}:\n{out}")
        parsed = json.loads(lines[0])
    if expect is not None:
        assert proc.returncode == expect, (
            f"expected exit {expect}, got {proc.returncode}\n"
            f"stdout: {out}\nstderr: {proc.stderr}")
    return proc.returncode, parsed, (proc.stderr or "")


def load_module(bed_dr):
    """Import the STAGED copy, so its self-anchored DURABLE_ROOT is the bed."""
    import importlib.util
    path = bed_dr / "scripts" / SCRIPT
    spec = importlib.util.spec_from_file_location("nd_under_test_%d" % time.time_ns(), path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(bed_dr / "scripts"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def seed_run(bed_dr, run_id="r1", passes=2, forms_by_unit=None,
             config="he.local.json"):
    """Write a run manifest and a complete, bound harvest -- the state --fold
    consumes -- without dispatching anything."""
    nd = load_module(bed_dr)
    manifest, manifest_sha1 = nd.load_manifest()
    units = nd.build_units(manifest)
    cfg = nd.resolve_particle_config(config)
    rm = nd.build_run_manifest(
        run_id=run_id, config_path=cfg,
        config_sha1_now=nd.sha1_bytes(cfg.read_bytes()),
        manifest_sha1=manifest_sha1, units=units, passes=passes,
        model=None, effort="low")
    rd = nd.run_dir_for(run_id)
    (rd / "harvest").mkdir(parents=True, exist_ok=True)
    nd.write_json_atomic(rd / "run-manifest.json", rm)
    for entry in rm["units"]:
        forms = (forms_by_unit or {}).get(entry["unit_id"], REPLY_FORMS)
        for p in range(1, passes + 1):
            nd.write_json_atomic(
                nd.harvest_path(run_id, entry["unit_id"], p),
                {"run_id": run_id, "unit": entry["unit_id"], "pass": p,
                 "source_sha1": entry["source_sha1"],
                 "prompt_sha1": rm["prompt_sha1"], "model": None, "effort": "low",
                 "forms": list(forms)})
    return nd, rm


def inventory_of(bed_dr, name="he.local.json"):
    doc = json.loads((bed_dr / "languages" / name).read_text(encoding="utf-8"))
    return doc.get("name_inventory")


# ===========================================================================
# Fixture self-checks. The substantive census and partition tests are
# meaningless if these stop holding, and a fixture that quietly drifts would
# make them pass vacuously -- so its own shape is asserted first.
# ===========================================================================

def test_fixture_has_an_always_subsumed_form(bed):
    """NACHMAN must occur in the corpus ONLY inside a longer inventory form.

    This is the case a census built on extract_candidate_spans() loses: pass 2
    emits at most one candidate per position, longest-first, so NACHMAN is
    never emitted anywhere in this book.
    """
    texts = [b["plain_text"] for b in BLOCKS.values() if b["plain_text"].strip()]
    occurrences = sum(t.count(NACHMAN) for t in texts)
    covered = sum(t.count(RABBI_NACHMAN) for t in texts)
    assert occurrences > 0, "the fixture no longer contains the subsumed form"
    assert occurrences == covered, (
        f"the subsumed form must occur ONLY inside the longer one "
        f"({occurrences} occurrences, {covered} covered) -- otherwise this "
        f"fixture stops exercising the subsumption case")


def test_fixture_manifest_matches_the_production_schema(bed):
    """The fixture is validated against the REAL manifest.schema.json, because a
    fixture that invents a shape the extractor never writes turns a production
    fatal into a green suite. That is exactly how a dict-shaped `segments`
    survived review here once."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (SCHEMAS_SRC / "manifest.schema.json").read_text(encoding="utf-8"))
    doc = json.loads((bed / "manifest.json").read_text(encoding="utf-8"))
    # The WHOLE schema, not a synthesized subset of the two properties this
    # feature reads: a subset validator cannot see a missing required key, so
    # the fixture would stay invalid while the assertion looked satisfied.
    jsonschema.Draft202012Validator(schema).validate(doc)
    assert isinstance(doc["segments"], list), "segments is an ARRAY, never a mapping"
    assert {r["seg"] for r in doc["segments"]} == {"seg01", "seg02"}
    print(f"segments={len(doc['segments'])} blocks={len(doc['blocks'])}")


def test_fixture_has_a_non_empty_block_no_segment_claims(bed):
    claimed = {bid for rec in SEGMENTS for bid in rec["block_ids"]}
    orphans = [bid for bid, b in BLOCKS.items()
               if bid not in claimed and b["plain_text"].strip()]
    assert orphans == ["FRONTBACK:fm01"], (
        f"the fixture must carry exactly one non-empty unclaimed block; got {orphans}")
    assert "seg" not in BLOCKS["HEAD:seg01"], (
        "HEAD:seg01 must carry NO seg field while still being a seg01 member -- "
        "that asymmetry is the whole reason units are built from block_ids")
    assert not BLOCKS["WS:seg02:0002"]["plain_text"].strip(), (
        "the fixture must keep a whitespace-only segment member")


# ===========================================================================
# A. Fold -- the correctness core
# ===========================================================================

def test_a1_occurrence_filter_keeps_what_occurs_and_drops_what_does_not(bed):
    seed_run(bed)
    _rc, out, err = run(bed, "--fold", "--run-id", "r1",
                        "--particle-config", "he.local.json", expect=0)
    got = set(inventory_of(bed))
    assert got == EXPECTED_SURVIVORS, f"inventory={sorted(got)}"
    assert ELIYAHU not in got, "a form occurring nowhere in the source must be dropped"
    # The subsumption case, stated as its own assertion so a regression names itself.
    assert NACHMAN in got, (
        "the always-subsumed form must SURVIVE -- it is what a census built on "
        "extract_candidate_spans() would silently lose")
    assert RABBI_NACHMAN in got
    # #238/#241 fold: space-joined vs maqaf-joined, unpointed vs pointed.
    assert MOSHE_LEIB in got, "the maqaf-joined occurrence must match the space-joined form"
    assert BRESLOV in got, "the pointed occurrence must match the unpointed form"
    assert out["surviving"] == len(EXPECTED_SURVIVORS)
    assert out["dropped"] == 1
    print(f"union={out['union_size']} surviving={out['surviving']} dropped={out['dropped']}")


@pytest.mark.parametrize("cause", ["missing", "not-utf8", "malformed", "duplicate-key",
                                   "unknown-key", "wrong-unit", "wrong-pass",
                                   "wrong-source-sha1", "wrong-prompt-sha1", "renamed"])
def test_a2_fold_refuses_an_unusable_or_unbound_harvest(bed, cause):
    nd, rm = seed_run(bed)
    victim = nd.harvest_path("r1", "seg02", 1)
    if cause == "missing":
        victim.unlink()
    elif cause == "not-utf8":
        victim.write_bytes(b"\xff\xfe not utf-8")
    elif cause == "malformed":
        victim.write_text("{not json", encoding="utf-8")
    elif cause == "duplicate-key":
        victim.write_text(
            '{"run_id":"r1","unit":"seg02","pass":1,"source_sha1":"x",'
            '"prompt_sha1":"y","model":null,"effort":"low",'
            '"forms":["%s"],"forms":[]}' % MOSHE_LEIB, encoding="utf-8")
    elif cause == "unknown-key":
        doc = json.loads(victim.read_text(encoding="utf-8"))
        doc["extra"] = 1
        victim.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    elif cause == "renamed":
        # A schema-valid harvest copied into ANOTHER expected slot. Filename
        # completeness is not input completeness.
        other = nd.harvest_path("r1", "seg01", 1)
        shutil.copy2(other, victim)
    else:
        field = {"wrong-unit": "unit", "wrong-pass": "pass",
                 "wrong-source-sha1": "source_sha1",
                 "wrong-prompt-sha1": "prompt_sha1"}[cause]
        doc = json.loads(victim.read_text(encoding="utf-8"))
        doc[field] = 99 if field == "pass" else "tampered"
        victim.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    rc, out, err = run(bed, "--fold", "--run-id", "r1",
                       "--particle-config", "he.local.json", expect=2)
    assert out is None, "a fatal must print NO stdout JSON"
    assert "FATAL name_discovery.py" in err
    assert inventory_of(bed) is None, "no inventory may be written on a refusal"


def test_a3_fold_refuses_a_changed_manifest_config_or_missing_run_manifest(bed):
    # (a) no run manifest at all
    rc, out, err = run(bed, "--fold", "--run-id", "nope",
                       "--particle-config", "he.local.json", expect=2)
    assert "no run manifest" in err and out is None

    # (b) manifest.json changed since dispatch
    nd, rm = seed_run(bed)
    doc = json.loads((bed / "manifest.json").read_text(encoding="utf-8"))
    doc["blocks"]["PARA:seg02:0001"]["plain_text"] += " " + ELIYAHU + "."
    (bed / "manifest.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rc, out, err = run(bed, "--fold", "--run-id", "r1",
                       "--particle-config", "he.local.json", expect=2)
    assert "manifest.json has changed" in err

    # (c) a bound tokenizer key changed since dispatch
    shutil.rmtree(bed / "runs")
    (bed / "manifest.json").write_text(
        json.dumps(manifest_doc(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    nd, rm = seed_run(bed)
    cfg = bed / "languages" / "he.local.json"
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    doc["STOPWORDS"] = doc["STOPWORDS"] + [ELIYAHU]
    cfg.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rc, out, err = run(bed, "--fold", "--run-id", "r1",
                       "--particle-config", "he.local.json", expect=2)
    assert "tokenizer semantics have changed" in err


def test_a3_expected_set_comes_from_the_run_manifest_not_a_listing(bed):
    nd, rm = seed_run(bed)
    # A stray extra harvest must not stand in for anything, and must not be read.
    stray = nd.harvest_path("r1", "seg01", 99)
    shutil.copy2(nd.harvest_path("r1", "seg01", 1), stray)
    nd.harvest_path("r1", "seg02", 1).unlink()
    rc, out, err = run(bed, "--fold", "--run-id", "r1",
                       "--particle-config", "he.local.json", expect=2)
    assert "is missing" in err


def test_a3_fold_refuses_a_changed_census_contract_or_unicode_database(bed):
    """The census IS the survival rule, and the token class is built from the
    running Unicode database -- so both are run identity, not provenance. A run
    dispatched before an upgrade and folded after it would otherwise score the
    same harvest under a different rule and report the old provenance."""
    nd, rm = seed_run(bed)
    census = bed / "scripts" / "language_smoke_report.py"
    census.write_text(census.read_text(encoding="utf-8") + "\n# nudged\n",
                      encoding="utf-8")
    rc, out, err = run(bed, "--fold", "--run-id", "r1",
                       "--particle-config", "he.local.json", expect=2)
    assert "owns the occurrence census" in err and out is None
    assert inventory_of(bed) is None

    # And the Unicode database, through the same identity path.
    shutil.rmtree(bed / "runs")
    census.write_text(census.read_text(encoding="utf-8").replace("\n# nudged\n", ""),
                      encoding="utf-8")
    nd, rm = seed_run(bed)
    rmf = bed / "runs" / "name-discovery" / "r1" / "run-manifest.json"
    doc = json.loads(rmf.read_text(encoding="utf-8"))
    doc["unidata_version"] = "0.0.0"
    rmf.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    rc, out, err = run(bed, "--fold", "--run-id", "r1",
                       "--particle-config", "he.local.json", expect=2)
    assert "Unicode database" in err and inventory_of(bed) is None


@pytest.mark.parametrize("tamper,needle", [
    ("unit-id-traversal", "does not match what the current"),
    ("unit-id-not-a-string", "does not match what the current"),
    ("units-not-a-list", "does not match what the current"),
    ("drop-a-unit", "does not match what the current"),
    ("drop-a-block-id", "does not match what the current"),
    ("passes-not-an-int", "`passes` is not a positive integer"),
    ("passes-is-a-bool", "`passes` is not a positive integer"),
])
def test_a11_fold_rebuilds_the_unit_set_instead_of_trusting_it(bed, tamper, needle):
    """`manifest_sha1` proves the manifest's BYTES; it says nothing about the
    `units` array, which lives in run-manifest.json and IS the completeness
    check's expected set. Validating each persisted unit_id does not cover the
    dangerous edit: DELETING an otherwise-valid entry leaves every survivor
    legal, its harvests stop being expected, and the fold exits 0 publishing an
    inventory missing every name unique to that unit. So the fold derives the
    set from the manifest it just verified and requires exact equality."""
    nd, _rm = seed_run(bed)
    rmf = bed / "runs" / "name-discovery" / "r1" / "run-manifest.json"
    doc = json.loads(rmf.read_text(encoding="utf-8"))
    if tamper == "unit-id-traversal":
        doc["units"][0]["unit_id"] = "../../../../../../etc/hosts"
    elif tamper == "unit-id-not-a-string":
        doc["units"][0]["unit_id"] = 17
    elif tamper == "units-not-a-list":
        doc["units"] = {"seg01": {}}
    elif tamper == "drop-a-unit":
        # The reproduction that motivated the rebuild: drop the reserved
        # __unsegmented__ unit, whose harvests stay on disk and stay valid.
        # Every remaining entry is legal, so no allowlist can see this.
        doc["units"] = [u for u in doc["units"]
                        if u["unit_id"] != nd.UNSEGMENTED_UNIT]
        assert len(doc["units"]) == 2, "the fixture must have had three units"
    elif tamper == "drop-a-block-id":
        doc["units"][0]["block_ids"] = doc["units"][0]["block_ids"][:-1]
    elif tamper == "passes-is-a-bool":
        # bool subclasses int, so `True` survives an isinstance check and reads
        # back as passes=1 -- a fold over the .1 harvests alone, committed with
        # every gate green. It must be refused like any other wrong shape.
        doc["passes"] = True
    else:
        doc["passes"] = "2"
    rmf.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    rc, out, err = run(bed, "--fold", "--run-id", "r1",
                       "--particle-config", "he.local.json", expect=2)
    assert needle in err, err
    assert out is None
    assert inventory_of(bed) is None, "nothing is written on a refused fold"


def test_a12_the_harvest_hash_encoding_is_injective(bed):
    """A space is legal INSIDE a name, so joining a form set with spaces is not
    an injective encoding: ["A", "B"] and ["A B"] produce identical bytes. Two
    genuinely different harvests would then share a harvest_set_sha1, and the
    committed-state shortcut would republish the FIRST fold's inventory and
    provenance for the second while reporting success."""
    nd, _rm = seed_run(bed)
    # SORTED ORDER MATTERS in constructing the collision: the buggy encoding is
    # " ".join(sorted(set(forms))), so the concatenation only collides with the
    # pair when it spells them in that order. Deriving `lo`/`hi` here rather
    # than hardcoding two names keeps the test a real collision probe instead of
    # an assertion that happens to hold for an unrelated reason.
    lo, hi = sorted([NACHMAN, MOSHE_LEIB])
    fused = lo + " " + hi
    assert " ".join(sorted({lo, hi})) == " ".join(sorted({fused})), (
        "the fixture must actually collide under the space-join encoding, or "
        "this test proves nothing about the encoding that replaced it")
    split = [{"unit": "seg01", "pass": 1, "forms": [lo, hi]}]
    joined = [{"unit": "seg01", "pass": 1, "forms": [fused]}]
    assert nd.harvest_set_sha1(split) != nd.harvest_set_sha1(joined), (
        "two forms and their space-joined concatenation must not hash alike")
    # The same property one level up: the sidecar's inventory_sha1.
    assert (nd.sha1_text(json.dumps([lo, hi], ensure_ascii=False))
            != nd.sha1_text(json.dumps([fused], ensure_ascii=False)))


def test_a12_an_edited_harvest_re_commits_rather_than_republishing(bed):
    """End to end, through the shortcut the hash keys: editing a slot from two
    forms to their space-joined concatenation must produce a DIFFERENT
    inventory, not a republish of the first fold's."""
    seed_run(bed, forms_by_unit={u: [NACHMAN, MOSHE_LEIB]
                                 for u in ("seg01", "seg02", "__unsegmented__")})
    run(bed, "--fold", "--run-id", "r1", "--particle-config", "he.local.json",
        expect=0)
    first = inventory_of(bed)
    assert NACHMAN in first
    side = bed / "runs" / "name-discovery" / "r1" / "name-discovery.json"
    first_set = json.loads(side.read_text(encoding="utf-8"))["harvest_set_sha1"]

    nd = load_module(bed)
    edited = nd.harvest_path("r1", "seg01", 1)
    doc = json.loads(edited.read_text(encoding="utf-8"))
    lo, hi = sorted([NACHMAN, MOSHE_LEIB])
    doc["forms"] = [lo + " " + hi]
    nd.write_json_atomic(edited, doc)
    rc, out, err = run(bed, "--fold", "--run-id", "r1",
                       "--particle-config", "he.local.json", expect=0)
    assert out["committed"] is True
    # The republish shortcut must NOT have fired. Its emit carries no
    # union_size -- that key is written only by a real commit -- so its presence
    # is the discriminator between "folded again" and "republished the old one".
    assert "union_size" in out, (
        "the edited harvest took the already-committed shortcut, which means "
        "the two harvests hashed alike")
    after = json.loads(side.read_text(encoding="utf-8"))["harvest_set_sha1"]
    assert after != first_set, (
        "the edited harvest must hash differently; identical hashes are exactly "
        "what makes the shortcut republish stale provenance")
    # The surviving inventory is unchanged here ON PURPOSE, and that is the
    # point: the other five slots still carry both forms, so the union does not
    # move. A hash collision would therefore be INVISIBLE in the published
    # result -- only the provenance would be wrong, silently.
    assert inventory_of(bed) == first


@pytest.mark.parametrize("edit,needle", [
    ("too-long", "over the"),
    ("too-many", "exceeds the"),
    ("control-char", "control character"),
    ("sentinel", "sentinel delimiter"),
    ("not-a-string", "non-empty string"),
    ("wrong-model", "not bound to this slot"),
    ("wrong-effort", "not bound to this slot"),
])
def test_a13_a_harvest_read_back_gets_the_same_contract_as_a_reply(bed, edit, needle):
    """The harvest is a FILE, and the workflow invites hand-editing it before
    the fold, so `--fold` reads form lists that never passed through
    parse_reply in this process. Every bound the reply boundary enforces has to
    hold on the way back in, or an edited artifact reaches the particle config
    through a door the model's own reply cannot use."""
    nd, _rm = seed_run(bed)
    path = nd.harvest_path("r1", "seg01", 1)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if edit == "too-long":
        doc["forms"] = ["a" * (nd.MAX_FORM_CHARS + 1)]
    elif edit == "too-many":
        doc["forms"] = ["f%d" % i for i in range(nd.MAX_FORMS_PER_REPLY + 1)]
    elif edit == "control-char":
        doc["forms"] = ["a\u200bb"]
    elif edit == "sentinel":
        doc["forms"] = ["a\u27e6FNREF\u27e7b"]
    elif edit == "not-a-string":
        doc["forms"] = [17]
    elif edit == "wrong-model":
        doc["model"] = "some-other-model"
    else:
        doc["effort"] = "high"
    nd.write_json_atomic(path, doc)
    rc, out, err = run(bed, "--fold", "--run-id", "r1",
                       "--particle-config", "he.local.json", expect=2)
    assert needle in err, err
    assert inventory_of(bed) is None, "nothing is published from a refused fold"


def test_a14_the_fold_target_must_be_the_run_s_own_config(bed):
    """Comparing tokenizer SEMANTICS is not comparing the target: any other
    .local.json with the same PARTICLES / STOPWORDS / has_elision / ELISION_RE
    passes that check, so a mistyped --particle-config would publish this run's
    inventory into a file the run was never dispatched against, rewrite it, and
    leave the intended one untouched while reporting success."""
    seed_run(bed)
    other = bed / "languages" / "other.local.json"
    shutil.copy2(bed / "languages" / "he.local.json", other)
    before = other.read_bytes()
    rc, out, err = run(bed, "--fold", "--run-id", "r1",
                       "--particle-config", "other.local.json", expect=2)
    assert "was dispatched against" in err, err
    assert other.read_bytes() == before, "the wrong config must not be rewritten"
    assert inventory_of(bed) is None and inventory_of(bed, "other.local.json") is None
    assert not (bed / "runs" / "name-discovery" / "r1"
                / "name-discovery.json").exists(), "no sidecar from a refused fold"


def execute_resume_plan(bed_dr, config="he.local.json", **fold_flags):
    """Run --resume-plan and then EXECUTE exactly what it emitted.

    The plan is only worth anything if following it works, and a test that
    calls --fold directly proves nothing about what the plan said. Returns
    (plan, [(command, rc, out, err), ...])."""
    _rc, plan, _err = run(bed_dr, "--resume-plan", "--particle-config", config,
                          expect=None)
    steps = {"fold": ["--fold"], "dispatch_then_fold": ["--dispatch", "--fold"],
             "fresh": [], "ambiguous": []}[plan["action"]]
    done = []
    for step in steps:
        argv = [step, "--run-id", plan["run_id"], "--particle-config", config]
        rc, out, err = run(bed_dr, *argv, expect=None)
        done.append((step, rc, out, err))
        if rc != 0:
            break
    return plan, done


def test_a15_resume_plan_on_a_clean_project_says_fresh(bed):
    rc, out, err = run(bed, "--resume-plan", "--particle-config", "he.local.json",
                       expect=0)
    assert out["action"] == "fresh" and out["run_id"] is None
    assert out["runs"] == []


def test_a15_resume_plan_names_an_incomplete_current_run(bed):
    seed_run(bed)
    rc, out, err = run(bed, "--resume-plan", "--particle-config", "he.local.json",
                       expect=0)
    assert out["action"] == "dispatch_then_fold" and out["run_id"] == "r1"


def test_a15_resume_plan_names_a_committed_run_for_fold_alone(bed):
    """A committed run REFUSES --dispatch (exit 2, run_committed), so the plan
    has to say `fold` and not `dispatch_then_fold` -- a chain starting with
    dispatch would halt before the fold and never re-enter."""
    seed_run(bed)
    run(bed, "--fold", "--run-id", "r1", "--particle-config", "he.local.json",
        expect=0)
    rc, out, err = run(bed, "--resume-plan", "--particle-config", "he.local.json",
                       expect=0)
    assert out["action"] == "fold" and out["run_id"] == "r1"
    # And the reason it must not be dispatch_then_fold, measured rather than
    # asserted from the docstring.
    rc2, _o2, err2 = run(bed, "--dispatch", "--run-id", "r1",
                         "--particle-config", "he.local.json", expect=2)
    assert "already committed a fold" in err2


def test_a15_a_stale_committed_run_never_shadows_its_replacement(bed):
    """THE mixed state a prose branch table gets backwards: run A commits, the
    manifest then changes, A's fold correctly refuses, and replacement run B is
    interrupted after its run manifest is written but before it commits. A has
    a sidecar and B does not, so a sidecar-first rule picks A -- which fails
    manifest_changed again -- and B is never resumed."""
    seed_run(bed, run_id="a")
    run(bed, "--fold", "--run-id", "a", "--particle-config", "he.local.json",
        expect=0)
    # The manifest moves, exactly as an operator editing the book would.
    doc = json.loads((bed / "manifest.json").read_text(encoding="utf-8"))
    doc["blocks"]["PARA:seg02:0001"]["plain_text"] += " " + ELIYAHU + " בא."
    doc["blocks"]["PARA:seg02:0001"]["sha1"] = hashlib.sha1(
        doc["blocks"]["PARA:seg02:0001"]["plain_text"].encode("utf-8")).hexdigest()
    (bed / "manifest.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # A is now stale, and says so.
    rc_a, _o, err_a = run(bed, "--fold", "--run-id", "a",
                          "--particle-config", "he.local.json", expect=2)
    assert "manifest.json has changed" in err_a
    # The replacement, dispatched against the NEW manifest and interrupted.
    seed_run(bed, run_id="b")
    rc, out, err = run(bed, "--resume-plan", "--particle-config", "he.local.json",
                       expect=0)
    assert out["action"] == "dispatch_then_fold", out
    assert out["run_id"] == "b", (
        "the stale committed run must not be chosen over the current incomplete one")
    by_id = {r["run_id"]: r for r in out["runs"]}
    assert by_id["a"]["committed"] is True and by_id["a"]["current"] is False
    assert "manifest.json has changed" in by_id["a"]["why"]
    assert by_id["b"]["current"] is True and by_id["b"]["committed"] is False


def test_a15_two_current_runs_are_refused_rather_than_ranked(bed):
    """Two runs current against the same inputs is an identity call. Picking one
    by timestamp would be this script deciding which run is the project's."""
    seed_run(bed, run_id="one")
    seed_run(bed, run_id="two")
    rc, out, err = run(bed, "--resume-plan", "--particle-config", "he.local.json",
                       expect=1)
    assert out["action"] == "ambiguous" and out["run_id"] is None
    assert {r["run_id"] for r in out["runs"] if r["current"]} == {"one", "two"}
    assert "identity call" in err


def test_a16_the_post_write_crash_window_is_planned_as_fold_and_the_plan_RUNS(bed):
    """A missing sidecar does not mean dispatch is safe. --fold writes its
    immutable backup, then the config, then the sidecar; a crash in between
    leaves the config REWRITTEN and no sidecar, and --dispatch for that run
    rebuilds the identity from the rewritten config and refuses on
    backup_sha1. So a dispatch-then-fold chain would halt before the fold that
    finishes publication. This test EXECUTES the emitted plan rather than
    calling --fold directly, which is the only way it can fail if the plan is
    wrong."""
    seed_run(bed)
    run(bed, "--fold", "--run-id", "r1", "--particle-config", "he.local.json",
        expect=0)
    rd = bed / "runs" / "name-discovery" / "r1"
    (rd / "name-discovery.json").unlink()          # the crash window
    assert (rd / "particle_config.before.json").is_file()

    plan, done = execute_resume_plan(bed)
    assert plan["action"] == "fold", plan
    entry = {r["run_id"]: r for r in plan["runs"]}["r1"]
    assert entry["committed"] is False and entry["publishing"] is True
    assert [(step, rc) for step, rc, _o, _e in done] == [("--fold", 0)], done
    assert (rd / "name-discovery.json").is_file(), (
        "following the plan must complete publication")

    # And the reason dispatch cannot lead: measured, not asserted from prose.
    (rd / "name-discovery.json").unlink()
    rc, _o, err = run(bed, "--dispatch", "--run-id", "r1",
                      "--particle-config", "he.local.json", expect=2)
    assert "backup_sha1" in err, err


def test_a16_a_committed_run_beside_a_current_unfinished_one_is_ambiguous(bed):
    """`committed or incomplete` hid an unfinished current run whenever one
    committed current run existed. That unfinished run may be the deliberate
    stochastic replacement meant to supersede the committed one, and nothing on
    disk says which -- so choosing the committed one is precisely the identity
    call this mode refuses to make."""
    seed_run(bed, run_id="a")
    run(bed, "--fold", "--run-id", "a", "--particle-config", "he.local.json",
        expect=0)
    seed_run(bed, run_id="b")
    rc, out, err = run(bed, "--resume-plan", "--particle-config", "he.local.json",
                       expect=1)
    assert out["action"] == "ambiguous" and out["run_id"] is None
    by_id = {r["run_id"]: r for r in out["runs"]}
    assert by_id["a"]["current"] and by_id["a"]["committed"]
    assert by_id["b"]["current"] and not by_id["b"]["committed"]
    assert "identity call" in err


def test_a16_a_fresh_project_plan_executes_to_nothing(bed):
    """The `fresh` action has no steps, and following it must not invent any."""
    plan, done = execute_resume_plan(bed)
    assert plan["action"] == "fresh" and done == []


def _reword_the_prompt(bed_dr):
    """Edit PROMPT_TEMPLATE in the STAGED copy, the way a plugin upgrade would.
    Rewording, not a whitespace nudge: prompt_identity_sha1 hashes the template
    and the point is that the model was asked a different question."""
    src = bed_dr / "scripts" / SCRIPT
    text = src.read_text(encoding="utf-8")
    marker = "--- TEXT BEGINS ---"
    assert marker in text, "the prompt template no longer carries its text fence"
    src.write_text(text.replace(marker, "--- SOURCE TEXT BEGINS ---", 1),
                   encoding="utf-8")


@pytest.mark.parametrize("route", ["plan", "direct-fold"])
def test_a17_prompt_drift_is_a_stale_run_on_every_route(bed, route):
    """`prompt_sha1` is in _identity_fields, so --dispatch already refuses to
    resume across a PROMPT_TEMPLATE change. Neither the planner nor the fold
    checked it, so the plan could emit a chain whose first step exits 2, and a
    direct --fold would publish a harvest gathered under the old question. The
    harvest's own prompt_sha1 binds it to the RUN MANIFEST, which says nothing
    about the current template."""
    seed_run(bed)
    _reword_the_prompt(bed)
    if route == "plan":
        plan, done = execute_resume_plan(bed)
        assert plan["action"] == "fresh", plan
        assert done == [], "a stale run must not be recommended at all"
        entry = {r["run_id"]: r for r in plan["runs"]}["r1"]
        assert entry["current"] is False
        assert "PROMPT_TEMPLATE has changed" in entry["why"]
    else:
        rc, out, err = run(bed, "--fold", "--run-id", "r1",
                           "--particle-config", "he.local.json", expect=2)
        assert "PROMPT_TEMPLATE has changed" in err
        assert inventory_of(bed) is None, "an old-prompt harvest must not publish"


def test_a17_dispatch_already_refused_prompt_drift(bed):
    """The boundary the other two routes were missing, measured here so the
    three are known to agree rather than assumed to."""
    seed_run(bed)
    _reword_the_prompt(bed)
    rc, out, err = run(bed, "--dispatch", "--run-id", "r1",
                       "--particle-config", "he.local.json", expect=2)
    assert "prompt_sha1" in err, err


def test_a18_a_changed_honorific_prefix_is_not_swallowed_by_the_shortcut(bed):
    """The committed-state shortcut keys on every input the sidecar's contents
    depend on. Honorific prefixes are a documented input to the dedup metrics
    the sidecar records, so a second fold with a different --honorific-prefix
    must NOT republish the first invocation's grouping and report success. A
    shortcut whose key is narrower than its output is a false green by
    construction."""
    seed_run(bed)
    run(bed, "--fold", "--run-id", "r1", "--particle-config", "he.local.json",
        expect=0)
    side = bed / "runs" / "name-discovery" / "r1" / "name-discovery.json"
    first = json.loads(side.read_text(encoding="utf-8"))
    assert first["honorific_groups"] == [] and first["honorific_prefixes"] == []
    inventory_before = inventory_of(bed)

    rc, out, err = run(bed, "--fold", "--run-id", "r1",
                       "--particle-config", "he.local.json",
                       "--honorific-prefix", RABBI, expect=0)
    after = json.loads(side.read_text(encoding="utf-8"))
    assert after["honorific_prefixes"] == [RABBI]
    grouped = {f for g in after["honorific_groups"] for f in g["forms"]}
    assert {RABBI_NACHMAN, NACHMAN} <= grouped, (
        "the union carries both spellings, so the prefix must produce a group")
    # It re-folded rather than republishing: union_size is written only by the
    # real commit path.
    assert "union_size" in out, "the changed prefix took the republish shortcut"
    # And grouping is metrics-only, exactly as documented.
    assert inventory_of(bed) == inventory_before, (
        "honorific grouping must never change inventory membership")

    # The shortcut still fires when nothing changed, including the prefixes.
    rc3, out3, _e3 = run(bed, "--fold", "--run-id", "r1",
                         "--particle-config", "he.local.json",
                         "--honorific-prefix", RABBI, expect=0)
    assert out3["rewrote_config"] is False and "union_size" not in out3


def test_a15_resume_plan_writes_nothing(bed):
    """Read-only: the plan is consulted before any decision, so it must not
    create, publish or mutate anything -- including on a committed project."""
    seed_run(bed)
    run(bed, "--fold", "--run-id", "r1", "--particle-config", "he.local.json",
        expect=0)
    before = {p: p.stat().st_mtime_ns for p in sorted(bed.rglob("*")) if p.is_file()}
    run(bed, "--resume-plan", "--particle-config", "he.local.json", expect=0)
    after = {p: p.stat().st_mtime_ns for p in sorted(bed.rglob("*")) if p.is_file()}
    assert before == after, "--resume-plan touched the tree"


def test_a15_resume_plan_rejects_a_run_id(bed):
    rc, out, err = run(bed, "--resume-plan", "--particle-config", "he.local.json",
                       "--run-id", "r1", expect=2)
    assert "meaningless with --resume-plan" in err


def test_a10_honorific_grouping_requires_a_token_boundary(bed):
    """`startswith` alone falsely groups an unrelated surname that merely begins
    with the prefix. Membership is unaffected either way -- nothing is ever
    dropped -- but a wrong provenance metric is still a wrong measurement."""
    RABINOVICH = RABBI + "נוביץ"
    # Mutate the source BEFORE seeding: the harvest is bound to the manifest's
    # per-unit source_sha1, so a run seeded against the old text would only be
    # thrown away again.
    doc = json.loads((bed / "manifest.json").read_text(encoding="utf-8"))
    doc["blocks"]["PARA:seg02:0001"]["plain_text"] = RABINOVICH + " בא."
    doc["blocks"]["PARA:seg02:0001"]["sha1"] = "x"
    (bed / "manifest.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    seed_run(bed, forms_by_unit={u: [RABBI_NACHMAN, NACHMAN, RABINOVICH]
                                 for u in ("seg01", "seg02", "__unsegmented__")})
    run(bed, "--fold", "--run-id", "r1", "--particle-config", "he.local.json",
        "--honorific-prefix", RABBI, expect=0)
    sidecar = json.loads((bed / "runs" / "name-discovery" / "r1"
                          / "name-discovery.json").read_text(encoding="utf-8"))
    grouped = {f for g in sidecar["honorific_groups"] for f in g["forms"]}
    assert RABINOVICH not in grouped, (
        "a surname that merely BEGINS with the prefix is not an honorific "
        "collapse -- no title was removed from it")
    assert RABINOVICH in set(inventory_of(bed)), "and it must still be in the inventory"


def test_a4_zero_survivors_is_a_gate_failure_and_writes_nothing(bed):
    seed_run(bed, forms_by_unit={u: [ELIYAHU] for u in
                                 ("seg01", "seg02", "__unsegmented__")})
    rc, out, err = run(bed, "--fold", "--run-id", "r1",
                       "--particle-config", "he.local.json", expect=1)
    assert out["surviving"] == 0 and out["committed"] is False
    assert "ZERO forms survived" in err
    assert inventory_of(bed) is None
    assert not (bed / "runs" / "name-discovery" / "r1" / "name-discovery.json").exists()
    # ... and --verify-inventory then refuses that state.
    run(bed, "--verify-inventory", "--particle-config", "he.local.json", expect=1)


def test_a5_other_four_keys_survive_and_a_prior_inventory_is_replaced(bed):
    cfg = bed / "languages" / "he.local.json"
    cfg.write_text(json.dumps(he_config(["קדם"]),
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    nd, rm = seed_run(bed)
    run(bed, "--fold", "--run-id", "r1", "--particle-config", "he.local.json", expect=0)
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert set(doc) == {"PARTICLES", "STOPWORDS", "has_elision", "ELISION_RE",
                        "name_inventory"}
    assert doc["STOPWORDS"] == ["של"] and doc["has_elision"] is False
    assert doc["ELISION_RE"] is None and doc["PARTICLES"] == []
    got = doc["name_inventory"]
    assert got == sorted(set(got)), "the inventory must be sorted and deduplicated"
    assert "קדם" not in got, (
        "a prior inventory is REPLACED, not merged -- merging would make a "
        "re-run's result depend on run order")
    backup = json.loads((bed / "runs" / "name-discovery" / "r1"
                         / "particle_config.before.json").read_text(encoding="utf-8"))
    assert backup["name_inventory"] == ["קדם"], (
        "the backup must hold the PRE-DISCOVERY bytes verbatim")


def test_a6_second_fold_republishes_and_a_changed_harvest_re_commits(bed):
    nd, rm = seed_run(bed)
    _rc, first, _e = run(bed, "--fold", "--run-id", "r1",
                         "--particle-config", "he.local.json", expect=0)
    cfg = bed / "languages" / "he.local.json"
    after_first = cfg.read_bytes()

    # (a) same harvest -> republish, no config rewrite, identical inventory hash
    _rc, again, err = run(bed, "--fold", "--run-id", "r1",
                          "--particle-config", "he.local.json", expect=0)
    assert again["rewrote_config"] is False
    assert again["inventory_sha1"] == first["inventory_sha1"]
    assert cfg.read_bytes() == after_first
    assert "already committed for exactly this harvest" in err

    # (b) a CHANGED harvest slot must NOT take the shortcut. Keying the
    #     shortcut on the config alone would republish a stale sidecar here.
    victim = nd.harvest_path("r1", "seg01", 2)
    doc = json.loads(victim.read_text(encoding="utf-8"))
    doc["forms"] = [RABBI_NACHMAN]          # drops NACHMAN from this slot
    victim.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _rc, third, _e = run(bed, "--fold", "--run-id", "r1",
                         "--particle-config", "he.local.json", expect=0)
    assert third["rewrote_config"] is True, (
        "a changed harvest must re-commit, never republish the old sidecar")


def test_a7_crash_between_config_and_sidecar_preserves_the_original_backup(bed):
    """The naive 'copy the current config each time' loses the pre-discovery
    bytes exactly here: on the re-run the 'current' config is already the new
    one. The backup is created once with O_EXCL and verified against a
    backup_sha1 computed BEFORE any dispatch."""
    nd, rm = seed_run(bed)
    run(bed, "--fold", "--run-id", "r1", "--particle-config", "he.local.json", expect=0)
    rd = bed / "runs" / "name-discovery" / "r1"
    backup_before = (rd / "particle_config.before.json").read_bytes()
    run_manifest_before = (rd / "run-manifest.json").read_bytes()

    # Simulate the crash: the config is committed, the sidecar never landed.
    (rd / "name-discovery.json").unlink()

    _rc, out, err = run(bed, "--fold", "--run-id", "r1",
                        "--particle-config", "he.local.json", expect=0)
    assert (rd / "name-discovery.json").is_file(), "the re-run must complete publication"
    assert (rd / "particle_config.before.json").read_bytes() == backup_before, (
        "the pre-discovery backup must NOT be overwritten by the recovery run")
    assert (rd / "run-manifest.json").read_bytes() == run_manifest_before, (
        "the run manifest is write-once; a backup_sha1 'filled by the first "
        "fold' would have required mutating it")
    assert "not re-copied" in err


def test_a8_dry_run_writes_nothing(bed):
    nd, rm = seed_run(bed)
    cfg = bed / "languages" / "he.local.json"
    before = cfg.read_bytes()
    before_mtime = cfg.stat().st_mtime_ns
    _rc, out, err = run(bed, "--fold", "--run-id", "r1",
                        "--particle-config", "he.local.json", "--dry-run", expect=0)
    assert cfg.read_bytes() == before and cfg.stat().st_mtime_ns == before_mtime
    rd = bed / "runs" / "name-discovery" / "r1"
    assert not (rd / "particle_config.before.json").exists()
    assert not (rd / "name-discovery.json").exists()
    assert out["dry_run"] is True and out["surviving"] == len(EXPECTED_SURVIVORS)


def test_a9_fold_refuses_a_config_that_is_not_a_project_local_override(bed):
    nd, rm = seed_run(bed, config="he.local.json")
    # he.json is the SHIPPED preset -- a plugin upgrade overwrites it.
    rc, out, err = run(bed, "--fold", "--run-id", "r1",
                       "--particle-config", "he.json", expect=2)
    assert "not a project-local override" in err and out is None


def test_a10_honorific_grouping_never_removes_a_member(bed):
    nd, rm = seed_run(bed)
    _rc, out, err = run(bed, "--fold", "--run-id", "r1",
                        "--particle-config", "he.local.json",
                        "--honorific-prefix", RABBI, expect=0)
    got = set(inventory_of(bed))
    assert {RABBI_NACHMAN, NACHMAN} <= got, (
        "both the bare and the title-bearing form must remain -- collapsing them "
        "is an identity call the IRON RULE reserves for the glossary adjudicator")
    sidecar = json.loads((bed / "runs" / "name-discovery" / "r1"
                          / "name-discovery.json").read_text(encoding="utf-8"))
    groups = sidecar["honorific_groups"]
    assert len(groups) == 1 and set(groups[0]["forms"]) == {RABBI_NACHMAN, NACHMAN}
    assert out["honorific_groups"] == 1


# ===========================================================================
# B. Units, dispatch, and the untrusted-writer boundary
# ===========================================================================

def test_b11_units_partition_the_manifests_non_empty_blocks(bed):
    nd = load_module(bed)
    manifest, _sha = nd.load_manifest()
    units = nd.build_units(manifest)
    by_id = {u["unit_id"]: u for u in units}
    assert set(by_id) == {"seg01", "seg02", nd.UNSEGMENTED_UNIT}
    # A seg-less member is dispatched ONCE, inside its segment.
    assert by_id["seg01"]["block_ids"] == ["HEAD:seg01", "PARA:seg01:0001"]
    assert "HEAD:seg01" not in by_id[nd.UNSEGMENTED_UNIT]["block_ids"]
    # The whitespace-only member is excluded without breaking coverage.
    assert by_id["seg02"]["block_ids"] == ["PARA:seg02:0001"]
    # The unclaimed non-empty block lands in the reserved bucket.
    assert by_id[nd.UNSEGMENTED_UNIT]["block_ids"] == ["FRONTBACK:fm01"]
    # Coverage: exactly once, over the census's own population.
    consumed = [b for u in units for b in u["block_ids"]]
    expected = {bid for bid, b in BLOCKS.items() if b["plain_text"].strip()}
    assert sorted(consumed) == sorted(expected)
    assert len(consumed) == len(set(consumed)), "no block may be dispatched twice"
    assert len(expected) == len(nd._inventory_scan_pieces(manifest))
    print(f"units={len(units)} blocks_consumed={len(consumed)}")


@pytest.mark.parametrize("mutation", ["dangling", "double-claim", "order-mismatch",
                                      "reserved-collision", "duplicate-seg-id",
                                      "segments-is-a-mapping"])
def test_b11_malformed_partitions_are_refused(bed, mutation):
    doc = json.loads((bed / "manifest.json").read_text(encoding="utf-8"))
    by_id = {rec["seg"]: rec for rec in doc["segments"]}
    if mutation == "dangling":
        by_id["seg01"]["block_ids"].append("PARA:seg01:9999")
        needle = "does not exist"
    elif mutation == "double-claim":
        by_id["seg02"]["block_ids"].append("PARA:seg01:0001")
        needle = "claimed by both"
    elif mutation == "order-mismatch":
        by_id["seg01"]["block_ids"] = ["PARA:seg01:0001", "HEAD:seg01"]
        needle = "disagrees with those blocks' order_index"
    elif mutation == "duplicate-seg-id":
        doc["segments"].append(dict(by_id["seg01"]))
        needle = "appears twice"
    elif mutation == "segments-is-a-mapping":
        # The shape a fixture invented once, and the production fatal it hid.
        doc["segments"] = {rec["seg"]: rec for rec in doc["segments"]}
        needle = "must be an ARRAY"
    else:
        doc["segments"].append({"seg": "__unsegmented__", "kind": "body",
                                "word_count": 1, "block_ids": ["FRONTBACK:fm01"]})
        needle = "reserves for the blocks no segment claims"
    (bed / "manifest.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rc, out, err = run(bed, "--fold", "--run-id", "r1",
                       "--particle-config", "he.local.json", expect=2)
    # The run manifest is absent here, so the refusal may come from either
    # gate; what must hold is that nothing is written and the exit is fatal.
    assert out is None
    nd = load_module(bed)
    manifest, _sha = nd.load_manifest()
    with pytest.raises(nd.NameDiscoveryError) as excinfo:
        nd.build_units(manifest)
    assert needle in str(excinfo.value)


def test_b13_dispatch_refuses_a_changed_run_identity_without_overwriting(bed):
    nd, rm = seed_run(bed, passes=2)
    manifest_file = bed / "runs" / "name-discovery" / "r1" / "run-manifest.json"
    before = manifest_file.read_bytes()
    for argv, field in (
        (["--passes", "3"], "passes"),
        (["--effort", "high"], "effort"),
        (["--model", "some-model"], "model"),
    ):
        rc, out, err = run(bed, "--dispatch", "--run-id", "r1",
                           "--particle-config", "he.local.json", *argv, expect=2)
        assert "identity differs" in err, f"{field}: {err[-400:]}"
        assert field in err
        assert manifest_file.read_bytes() == before, "the run manifest is write-once"


def test_b13_dispatch_refuses_a_committed_run(bed):
    nd, rm = seed_run(bed)
    run(bed, "--fold", "--run-id", "r1", "--particle-config", "he.local.json", expect=0)
    rc, out, err = run(bed, "--dispatch", "--run-id", "r1",
                       "--particle-config", "he.local.json", "--passes", "2", expect=2)
    assert "already committed a fold" in err and out is None


def test_b14_a_second_invocation_exits_2_while_the_lease_is_held(bed):
    nd, rm = seed_run(bed)
    # PROJECT-scoped, not run-scoped: every run's fold read-modify-writes the
    # SAME particle_config, so serialising only within one run id would leave
    # exactly the interleaving the lock exists to remove.
    lock = bed / "runs" / "name-discovery" / ".name_discovery.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    import fcntl
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        rc, out, err = run(bed, "--fold", "--run-id", "r1",
                           "--particle-config", "he.local.json", expect=2)
        assert "holds the lease" in err and out is None
        assert inventory_of(bed) is None, "a lease loser writes no run artifact"
        # A DIFFERENT run id is excluded too -- that is the whole point.
        rc, out, err = run(bed, "--fold", "--run-id", "other",
                           "--particle-config", "he.local.json", expect=2)
        assert "holds the lease" in err and out is None
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_b15_a_durable_root_under_a_temp_root_is_refused_before_any_launch(bed,
                                                                          monkeypatch):
    # Point TMPDIR back AT the bed, which is what a real machine looks like when
    # the operator put the project inside the temp dir.
    monkeypatch.setenv("TMPDIR", str(bed.parent))
    rc, out, err = run(bed, "--dispatch", "--run-id", "fresh",
                       "--particle-config", "he.local.json",
                       env={"TMPDIR": str(bed.parent)}, expect=2)
    assert "codex makes writable under workspace-write" in err
    assert out is None
    assert not (bed / "runs" / "name-discovery" / "fresh" / "run-manifest.json").exists()


@pytest.mark.parametrize("bad", ["too-many-forms", "form-too-long", "control-char",
                                 "lone-surrogate",
                                 "sentinel-delimiter", "duplicate-key", "unknown-key",
                                 "not-an-object", "forms-not-array", "empty-form"])
def test_b17_reply_bounds_reject_at_their_boundary(bed, bad):
    nd = load_module(bed)
    if bad == "too-many-forms":
        payload = json.dumps({"forms": [f"a{i}" for i in range(nd.MAX_FORMS_PER_REPLY + 1)]})
        needle = "exceeds the"
    elif bad == "form-too-long":
        payload = json.dumps({"forms": ["a" * (nd.MAX_FORM_CHARS + 1)]})
        needle = "over the"
    elif bad == "control-char":
        payload = json.dumps({"forms": ["a​b"]})
        needle = "control character"
    elif bad == "lone-surrogate":
        # Valid JSON, decodes to a str, and cannot be UTF-8 encoded: without this
        # bound the form reaches the harvest write and fails it THERE, turning a
        # bad reply into a mid-write error instead of a refused one.
        payload = '{"forms":["A\\ud800B"]}'
        needle = "lone surrogate"
    elif bad == "sentinel-delimiter":
        payload = json.dumps({"forms": ["a⟦FNREF⟧b"]}, ensure_ascii=False)
        needle = "sentinel delimiter"
    elif bad == "duplicate-key":
        payload = '{"forms":["a"],"forms":[]}'
        needle = "repeats the member name"
    elif bad == "unknown-key":
        payload = json.dumps({"forms": ["a"], "notes": "x"})
        needle = "ONLY key is 'forms'"
    elif bad == "not-an-object":
        payload = json.dumps(["a"])
        needle = "ONLY key is 'forms'"
    elif bad == "forms-not-array":
        payload = json.dumps({"forms": "a"})
        needle = "must be an array"
    else:
        payload = json.dumps({"forms": ["  "]})
        needle = "non-empty string"
    with pytest.raises(nd.NameDiscoveryError) as excinfo:
        nd.parse_reply(payload.encode("utf-8"), "unit.1")
    assert needle in str(excinfo.value)


def test_b17_an_oversized_reply_is_refused_not_truncated(bed, tmp_path):
    """Refused, never truncated: a truncated reply parses as a SHORTER valid form
    list, which would thin the union with every gate green."""
    nd = load_module(bed)
    with pytest.raises(nd.NameDiscoveryError) as excinfo:
        nd.parse_reply(b"x" * (nd.MAX_REPLY_BYTES + 1), "unit.1")
    assert "exceeds" in str(excinfo.value)
    # And the read refuses before parsing, so the cap is not merely a parse-time
    # check over bytes already in memory.
    box = tmp_path / "over"
    box.mkdir()
    reply = box / "names.json"
    reply.write_bytes(b"x" * (nd.MAX_REPLY_BYTES + 1))
    with pytest.raises(nd.NameDiscoveryError) as excinfo:
        nd.read_sandbox_reply(reply, "unit.1")
    assert "refused rather than truncated" in str(excinfo.value)
    # The three caps are INDEPENDENT, and each has input the other two accept.
    # First: the largest ASCII reply within both structural caps stays under the
    # byte cap, and must be ACCEPTED -- otherwise a legitimate maximal reply is a
    # failed slot. The forms are kept DISTINCT on purpose: truncating a distinct
    # suffix away collapses the payload to one repeated string, and the
    # assertion then passes over 1 form rather than 2000.
    forms = [("a" * nd.MAX_FORM_CHARS)[:-len(str(i))] + str(i)
             for i in range(nd.MAX_FORMS_PER_REPLY)]
    assert len(set(forms)) == nd.MAX_FORMS_PER_REPLY, "the payload must be distinct"
    assert {len(f) for f in forms} == {nd.MAX_FORM_CHARS}, "each form is AT the cap"
    biggest = json.dumps({"forms": forms}).encode("utf-8")
    assert len(biggest) < nd.MAX_REPLY_BYTES, (
        f"the largest ASCII well-formed reply is {len(biggest)} bytes, which must "
        f"stay under the {nd.MAX_REPLY_BYTES}-byte cap")
    assert nd.parse_reply(biggest, "unit.1") == sorted(set(forms))
    # One more form is refused, by the count cap rather than the byte cap.
    with pytest.raises(nd.NameDiscoveryError) as excinfo:
        nd.parse_reply(json.dumps({"forms": forms + ["z"]}).encode("utf-8"), "unit.1")
    assert "exceeds the" in str(excinfo.value)
    # Second, and this is why the byte cap is not redundant machinery: the same
    # shape in NON-ASCII, JSON-escaped the way a model reply legitimately may be,
    # is six bytes per character and blows the byte cap while satisfying BOTH
    # structural caps. Valid JSON, within every other bound, refused on bytes.
    heb = [("ר" * nd.MAX_FORM_CHARS)[:-len(str(i))] + str(i)
           for i in range(nd.MAX_FORMS_PER_REPLY)]
    escaped = json.dumps({"forms": heb}, ensure_ascii=True).encode("utf-8")
    assert len(escaped) > nd.MAX_REPLY_BYTES, (
        f"expected an escaped maximal reply to exceed the cap; got {len(escaped)}")
    with pytest.raises(nd.NameDiscoveryError) as excinfo:
        nd.parse_reply(escaped, "unit.1")
    assert "exceeds" in str(excinfo.value), (
        "the byte cap must be the bound that refuses it, not a structural cap")
    print(f"ascii_bytes={len(biggest)} escaped_bytes={len(escaped)} "
          f"cap={nd.MAX_REPLY_BYTES} forms={len(forms)}")


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_b16_a_symlink_or_fifo_at_the_reply_path_is_refused(bed, tmp_path, kind):
    nd = load_module(bed)
    box = tmp_path / "box"
    box.mkdir()
    target = box / "real.json"
    target.write_text(json.dumps({"forms": ["x"]}), encoding="utf-8")
    reply = box / "names.json"
    if kind == "symlink":
        reply.symlink_to(target)
    else:
        os.mkfifo(str(reply))
    with pytest.raises(nd.NameDiscoveryError) as excinfo:
        nd.read_sandbox_reply(reply, "unit.1")
    assert "not a regular file" in str(excinfo.value)


def test_b19_an_unresolvable_companion_is_fatal_with_no_harvest(bed):
    (bed / "scripts" / "resolve_codex_companion.py").unlink()
    rc, out, err = run(bed, "--dispatch", "--run-id", "fresh",
                       "--particle-config", "he.local.json", expect=2)
    assert "resolve_codex_companion.py not found" in err and out is None
    assert not (bed / "runs" / "name-discovery" / "fresh" / "harvest").exists()


@pytest.mark.parametrize("run_id", ["a/b", "..", "z..poison", ".hidden", "a:b", ""])
def test_b21_an_unsafe_run_id_is_refused_before_any_path_is_built(bed, run_id):
    rc, out, err = run(bed, "--fold", "--run-id", run_id,
                       "--particle-config", "he.local.json", expect=2)
    assert out is None
    assert "run id" in err or "--run-id is required" in err
    assert not (bed / "runs").exists(), "no path may be constructed from an unsafe run id"


# ===========================================================================
# C. --verify-inventory
# ===========================================================================

def test_c22_verify_inventory_exit_codes(bed):
    # empty -> 1, naming the path and the count
    _rc, out, err = run(bed, "--verify-inventory",
                        "--particle-config", "he.local.json", expect=1)
    assert out["n_inventory"] == 0 and out["ok"] is False
    assert "he.local.json" in err and "EMPTY name_inventory" in err
    assert str(bed / "languages" / "he.local.json") == out["path"]

    # non-empty -> 0
    cfg = bed / "languages" / "he.local.json"
    cfg.write_text(json.dumps(he_config([NACHMAN]), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    _rc, out, _e = run(bed, "--verify-inventory",
                       "--particle-config", "he.local.json", expect=0)
    assert out["n_inventory"] == 1 and out["ok"] is True

    # unloadable config -> 2
    cfg.write_text("{not json", encoding="utf-8")
    rc, out, err = run(bed, "--verify-inventory",
                       "--particle-config", "he.local.json", expect=2)
    assert out is None


@pytest.mark.parametrize("bad", ["he/../he.local.json", "sub/he.local.json", "he.local",
                                 "he.local.json.bak"])
def test_c22_verify_inventory_refuses_a_non_bare_filename(bed, bad):
    rc, out, err = run(bed, "--verify-inventory", "--particle-config", bad, expect=2)
    assert out is None
    assert "BARE filename" in err or "not found" in err


def test_c23_a_failed_discovery_cannot_reach_bootstrap_names(bed):
    """End-to-end over the REAL shipped scripts: a failed dispatch, then a
    failed fold, then --verify-inventory. The inventory stays empty, so W3's
    chain stops before bootstrap_names.py -- which has no discovery
    prerequisite and would otherwise exit 0 over an empty result."""
    # dispatch fails: no companion resolvable in this bed
    (bed / "scripts" / "resolve_codex_companion.py").unlink()
    rc_dispatch, _o, _e = run(bed, "--dispatch", "--run-id", "e2e",
                              "--particle-config", "he.local.json")
    assert rc_dispatch != 0

    # fold fails: there is no complete harvest
    rc_fold, _o, _e = run(bed, "--fold", "--run-id", "e2e",
                          "--particle-config", "he.local.json")
    assert rc_fold != 0

    # the gate refuses, and the inventory is still empty
    rc_verify, out, err = run(bed, "--verify-inventory",
                              "--particle-config", "he.local.json", expect=1)
    assert out["n_inventory"] == 0
    assert inventory_of(bed) is None
    assert "do NOT proceed to" in err

    # And the fact that makes the gate necessary, asserted rather than assumed:
    # bootstrap_names.py itself exits 0 over this state.
    proc = subprocess.run(
        [sys.executable, str(bed / "scripts" / "bootstrap_names.py"),
         "--particle-config", "he.local.json"],
        capture_output=True, text=True, timeout=TIMEOUT)
    assert proc.returncode == 0, (
        "bootstrap_names.py is expected to exit 0 here -- that is exactly why "
        "--verify-inventory has to run BEFORE it")
    candidates = json.loads((bed / "name_candidates.json").read_text(encoding="utf-8"))
    assert candidates["n_candidates"] == 0, (
        "an uncased source with no inventory yields a STRUCTURAL zero, which is "
        "the degradation this feature removes")


# ===========================================================================
# D. Contract / house style
# ===========================================================================

def _module_text(name):
    return (SCRIPTS_SRC / name).read_text(encoding="utf-8")


def _tuple_members(text, name):
    """The literal string members of a module-level tuple, read with ast rather
    than by slicing text: cache_key.py's tuples are surrounded by long comment
    blocks that mention the very filenames a text slice would then match."""
    import ast
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if name in targets and isinstance(node.value, ast.Tuple):
                return [e.value for e in node.value.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    raise AssertionError(f"{name} not found as a module-level tuple assignment")


def test_d25_a_fatal_prints_no_stdout_json(bed):
    rc, out, err = run(bed, "--fold", "--run-id", "absent",
                       "--particle-config", "he.local.json", expect=2)
    assert out is None, "a fatal must print NO stdout JSON"
    assert err.startswith("FATAL name_discovery.py"), err[:200]


@pytest.mark.parametrize("owner,var", [
    ("review_artifact_check.py", "_SEG_ID_RE"),
    ("claim_record.py", "_RUN_ID_DIR_RE"),
])
def test_d26_copied_regex_literals_are_byte_identical_to_their_owner(owner, var):
    import re as _re
    mine = _module_text(SCRIPT)
    theirs = _module_text(owner)
    pattern = _re.compile(r"^%s = re\.compile\(.*\)$" % _re.escape(var), _re.M)
    a = pattern.search(mine)
    b = pattern.search(theirs)
    assert a and b, f"{var} not found in one of the two files"
    assert a.group(0) == b.group(0), (
        f"{var} has drifted from {owner}'s copy:\n{a.group(0)}\n{b.group(0)}")


@pytest.mark.parametrize("owner,func", [
    ("review_artifact_check.py", "validate_seg"),
    ("claim_record.py", "validate_run_id"),
    ("canon_link_groups.py", "_reject_duplicate_keys"),
])
def test_d26_copied_decisions_agree_with_their_owner(owner, func):
    """The DECISION, not the docstring: each copy is compared by executing both
    over a shared probe set. A copy carrying only the pattern accepts inputs the
    owner refuses (`z..poison`), which is why agreement on the regex is not
    agreement on the answer."""
    import importlib.util
    mods = {}
    for name in (SCRIPT, owner):
        spec = importlib.util.spec_from_file_location(
            "drift_%s" % name.replace(".", "_"), SCRIPTS_SRC / name)
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(SCRIPTS_SRC))
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.path.pop(0)
        mods[name] = mod
    mine = getattr(mods[SCRIPT], func)
    theirs = getattr(mods[owner], func)
    if func == "_reject_duplicate_keys":
        probes = [[("a", 1), ("b", 2)], [("a", 1), ("a", 2)], []]
        for probe in probes:
            outcomes = []
            for fn in (mine, theirs):
                try:
                    outcomes.append(("ok", fn(list(probe))))
                except ValueError as exc:
                    outcomes.append(("raised", exc.args[0]))
            assert outcomes[0] == outcomes[1], f"{func} disagrees on {probe}"
        return
    probes = ["seg01", "FRONTBACK:fm01", "a/b", "..", "z..poison", ".hidden",
              "a:b", "", "seg;rm", "a b", "A1_b", "."]
    for probe in probes:
        got_mine = mine(probe)
        got_theirs = theirs(probe)
        assert (got_mine is None) == (got_theirs is None), (
            f"{func} disagrees with {owner} on {probe!r}: "
            f"{got_mine!r} vs {got_theirs!r}")


def test_d27_the_contract_docstring_lists_exactly_what_is_imported(bed):
    """Parsed, not grepped: the docstring's CONTRACT block is compared against
    the module's real import of language_smoke_report."""
    import ast
    text = _module_text(SCRIPT)
    tree = ast.parse(text)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "language_smoke_report":
            imported |= {alias.name for alias in node.names}
    assert imported, "the census import was not found"
    doc = ast.get_docstring(tree) or ""
    contract = doc[doc.index("CONTRACT with language_smoke_report.py"):]
    missing = sorted(n for n in imported if n not in contract)
    assert not missing, (
        f"the CONTRACT block does not document {missing}; a reader relying on it "
        f"would not know what this script depends on")
    print(f"imported={sorted(imported)}")


def test_d27_a_missing_census_dependency_is_this_scripts_own_named_fatal(bed):
    """A missing dependency must be attributed to name_discovery.py, not surface
    under the imported module's own import-time exit."""
    (bed / "scripts" / "language_smoke_report.py").unlink()
    rc, out, err = run(bed, "--verify-inventory",
                       "--particle-config", "he.local.json", expect=2)
    assert out is None, "a fatal prints NO stdout JSON"
    assert err.startswith("FATAL name_discovery.py"), (
        "the failure must be attributed to THIS script, not surface under the "
        "imported module's own import-time exit")
    assert "cannot import the occurrence census" in err
    assert "no fallback census" in err


@pytest.mark.parametrize("path", ["bootstrap_names.py", "segpack.py", "cache_key.py",
                                  "glossary_batch_plan.py"])
def test_d28_the_files_this_change_must_not_touch_are_unmodified(path):
    """A REAL blob comparison against origin/main, not a tuple-membership proxy:
    tuple equality says nothing about whether either file's BYTES changed, and
    those bytes are what derivation_bundle_hash and plugin_bundle_hash cover.

    Skips loudly when origin/main cannot be resolved, and FAILS if that skip
    fires in CI -- where full history is checked out and origin/main always
    resolves -- so it can never be a silent pass.
    """
    rel = f"plugins/literary-translator/skills/literary-translator/assets/scripts/{path}"
    repo = PLUGIN_ROOT.parent.parent
    probe = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify",
                            "origin/main"], capture_output=True, text=True, timeout=60)
    if probe.returncode != 0:
        if os.environ.get("CI"):
            pytest.fail(
                "origin/main does not resolve, but this is CI -- the workflow "
                "checks out full history, so this must never be skipped here")
        pytest.skip("origin/main does not resolve in this checkout; run "
                    "`git fetch origin main` to enable this guard")
    show = subprocess.run(["git", "-C", str(repo), "show", f"origin/main:{rel}"],
                          capture_output=True, timeout=60)
    assert show.returncode == 0, f"could not read origin/main:{rel}"
    assert (SCRIPTS_SRC / path).read_bytes() == show.stdout, (
        f"{path} differs from origin/main. This feature must not edit it: "
        f"bootstrap_names.py and segpack.py are the DERIVATION_BUNDLE_MEMBERS "
        f"(editing either re-stales every project's converged segments) and "
        f"cache_key.py / glossary_batch_plan.py are PLUGIN_BUNDLE_MEMBERS.")


def test_d28_no_bundle_membership_was_changed():
    """The membership CLAIM, checked as a membership claim -- paired with the
    byte guard above, which is what checks the files themselves."""
    text = _module_text("cache_key.py")
    plugin_members = _tuple_members(text, "PLUGIN_BUNDLE_MEMBERS")
    assert plugin_members, "the tuple parsed empty -- the reader is broken, not the claim"
    assert SCRIPT not in plugin_members, (
        "name_discovery.py must NOT join PLUGIN_BUNDLE_MEMBERS: registration "
        "moves plugin_bundle_hash for every cased and discovery-disabled project "
        "while not actually forcing rediscovery -- a bundle mismatch stales "
        "segment cache keys, it does not require the inventory to be regenerated")
    assert "language_smoke_report.py" not in plugin_members
    assert _tuple_members(text, "DERIVATION_BUNDLE_MEMBERS") == [
        "bootstrap_names.py", "segpack.py"]
    print(f"plugin_bundle_members={len(plugin_members)}")


def test_d29_profile_schema_accepts_the_new_block_and_rejects_an_unknown_key(bed):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((SCHEMAS_SRC / "profile.schema.json").read_text(encoding="utf-8"))
    block = schema["properties"]["glossary"]["properties"]["name_discovery"]
    assert block["additionalProperties"] is False
    assert set(block["properties"]) == {
        "enabled", "passes", "effort", "max_parallel", "honorific_prefixes"}
    validator = jsonschema.Draft202012Validator(block)
    validator.validate({"enabled": True, "passes": 6, "effort": "low",
                        "max_parallel": 6, "honorific_prefixes": [RABBI]})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"enabled": True, "unknown": 1})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"passes": 0})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"effort": "ultra"})


def test_the_example_profile_ships_no_new_choose_sentinel():
    """A CHOOSE_ sentinel would require a matching KNOB_QUESTIONS entry, held
    equal by profile_validate.py's own two-way drift guard. The discovery block
    ships commented out instead, so that script needs no edit."""
    text = (ASSETS / "profile.example.yml").read_text(encoding="utf-8")
    assert "name_discovery" in text, "the example must document the new block"
    for line in text.splitlines():
        if "name_discovery" in line or "honorific_prefixes" in line:
            assert line.lstrip().startswith("#"), (
                f"the discovery block must ship commented out: {line!r}")
    block_lines = [ln for ln in text.splitlines()
                   if "CHOOSE_" in ln and "name_discovery" in ln]
    assert not block_lines, f"no CHOOSE_ sentinel may be added: {block_lines}"


def test_the_w3_chain_is_wired_before_the_smoke_test_and_both_glossary_branches():
    """Placement is the half a CLI test cannot see. Inside the
    `glossary.enabled: false` branch, a glossary-ENABLED project would take the
    `Otherwise` clause and skip discovery entirely; after the smoke test, the
    report would certify a config the fold then replaces."""
    skill = (ASSETS.parent / "SKILL.md").read_text(encoding="utf-8")
    w2_end = skill.index("Read either only when\n`source.conservation` is declared.")
    opener = skill.index("**W3 OPENS with LLM name discovery")
    verify = skill.index("--verify-inventory")
    w3_smoke_para = skill.index("**W3 Bootstrap style bible + language smoke test.**")
    smoke = skill.index("If no matching report exists, run the mandatory smoke test")
    disabled_branch = skill.index("**`glossary.enabled: false` — the project declared")
    otherwise = skill.index("Otherwise (`glossary.enabled` not false, the default)")
    assert w2_end < opener < verify < w3_smoke_para < smoke, (
        "the discovery chain must open W3 -- after W2's last paragraph and BEFORE "
        "the three-hash/smoke-test paragraph, whose report would otherwise certify "
        "a particle_config the fold then replaces")
    assert verify < disabled_branch < otherwise, (
        "the discovery chain must sit before BOTH glossary branches: inside the "
        "`glossary.enabled: false` branch, a glossary-ENABLED project would take "
        "the `Otherwise` clause and skip discovery entirely")
    print(f"w2_end={w2_end} opener={opener} verify={verify} "
          f"smoke_para={w3_smoke_para} disabled={disabled_branch} otherwise={otherwise}")


# ===========================================================================
# B.18 / B.12 / B.20 -- the launch path, exercised through a shim that plays
# codex-companion. No test calls a real model.
#
# The shim also launches a distinct detached child whose argv matches the REAL
# teardown selector (app-server-broker.mjs ... --cwd <sandbox>) and does not
# return until `pgrep` can see it. That handshake is the whole reason this test
# is deterministic: Popen can return before a child's argv is visible, a race
# tests/sandbox_broker_teardown.test.py already documents and polls around.
# Recording the SHIM's own pid would prove only that the foreground shim
# exited, which is not what the teardown targets.
# ===========================================================================

SHIM = r'''#!/usr/bin/env python3
"""Stands in for `node codex-companion.mjs`. argv[1] is the companion path."""
import json, os, subprocess, sys, time, pathlib

argv = sys.argv[2:]
mode = argv[0] if argv else ""


def flag(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


cwd = flag("--cwd")
record = pathlib.Path(os.environ["SHIM_RECORD"])

if mode == "status":
    print(json.dumps({"job": {"status": "completed"}}))
    sys.exit(0)

if mode != "task":
    sys.exit(2)

forms = json.loads(os.environ.get("SHIM_FORMS", "[]"))
mangle = os.environ.get("SHIM_MANGLE", "")
sandbox = pathlib.Path(cwd)

if mangle == "no-reply":
    print(json.dumps({"jobId": "job-%d" % os.getpid()}))
    sys.exit(0)
if mangle == "no-jobid":
    print(json.dumps({"ok": True}))
    sys.exit(0)
if mangle == "launch-fails":
    sys.stderr.write("simulated capacity failure\n")
    sys.exit(1)

# The broker: a real detached process whose argv the production selector
# matches. `pgrep -f` sees the full command line, so the marker file only has
# to EXIST for node -- here, python -- to run it.
broker_src = sandbox.parent / "app-server-broker.mjs"
if not broker_src.exists():
    broker_src.write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
child = subprocess.Popen(
    [sys.executable, str(broker_src), "serve", "--cwd", str(sandbox)],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

pattern = "app-server-broker\\.mjs .*--cwd %s( |$)" % str(sandbox).replace(".", "\\.")
deadline = time.monotonic() + 20
visible = False
while time.monotonic() < deadline:
    probe = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    if probe.returncode == 0 and str(child.pid) in probe.stdout.split():
        visible = True
        break
    time.sleep(0.1)

with record.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"sandbox": str(sandbox), "broker_pid": child.pid,
                         "visible": visible}) + "\n")

if mangle == "bad-json":
    (sandbox / "names.json").write_text("{not json", encoding="utf-8")
else:
    (sandbox / "names.json").write_text(
        json.dumps({"forms": forms}, ensure_ascii=False), encoding="utf-8")
print(json.dumps({"jobId": "job-%d" % os.getpid()}))
'''


@pytest.fixture
def shim(bed, tmp_path):
    """A staged companion shim plus a fake resolver that points at it."""
    path = tmp_path / "shim.py"
    path.write_text(SHIM, encoding="utf-8")
    path.chmod(0o755)
    companion = tmp_path / "codex-companion.mjs"
    companion.write_text("// placeholder\n", encoding="utf-8")
    resolver = bed / "scripts" / "resolve_codex_companion.py"
    resolver.write_text(
        "#!/usr/bin/env python3\nimport json\n"
        "print(json.dumps({'companion_path': %r}))\n" % str(companion),
        encoding="utf-8")
    resolver.chmod(0o755)
    record = tmp_path / "shim-record.jsonl"
    return {"node": str(path), "record": record}


def _shim_env(shim, forms, mangle=""):
    return {"SHIM_RECORD": str(shim["record"]),
            "SHIM_FORMS": json.dumps(forms),
            "SHIM_MANGLE": mangle}


def _records(shim):
    if not shim["record"].exists():
        return []
    return [json.loads(ln) for ln in
            shim["record"].read_text(encoding="utf-8").splitlines() if ln.strip()]


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_b18_dispatch_writes_a_bound_harvest_and_leaves_no_broker(bed, shim):
    rc, out, err = run(bed, "--dispatch", "--run-id", "d1",
                       "--particle-config", "he.local.json",
                       "--passes", "2", "--max-parallel", "2",
                       "--node", shim["node"],
                       env=_shim_env(shim, REPLY_FORMS), expect=0)
    assert out["units"] == 3 and out["passes"] == 2
    assert out["dispatched"] == 6 and out["reused"] == 0 and out["failed"] == 0

    recs = _records(shim)
    assert len(recs) == 6, f"expected 6 launches, got {len(recs)}"
    assert all(r["visible"] for r in recs), (
        "the shim's own pgrep handshake did not see its broker child -- the "
        "teardown assertion below would be racing an invisible process")
    # Every sandbox is distinct and single-use.
    assert len({r["sandbox"] for r in recs}) == 6

    # THE ASSERTION: no broker keyed to a job's sandbox survives the driver.
    # Bounded poll, because SIGTERM does not prove exit.
    deadline = time.monotonic() + 15
    survivors = [r["broker_pid"] for r in recs]
    while time.monotonic() < deadline and survivors:
        survivors = [pid for pid in survivors if _alive(pid)]
        if survivors:
            time.sleep(0.2)
    assert not survivors, (
        f"broker pid(s) {survivors} outlived their sandbox; at 211 segments x 6 "
        f"passes that is 1266 leaked brokers, the leak the glossary driver "
        f"measured at 2794 state directories in one day")

    # The sandboxes themselves are gone too.
    for rec in recs:
        assert not Path(rec["sandbox"]).exists(), f"{rec['sandbox']} survived"

    # And the harvest it wrote is bound, so --fold accepts it.
    _rc, folded, _e = run(bed, "--fold", "--run-id", "d1",
                          "--particle-config", "he.local.json", expect=0)
    assert set(inventory_of(bed)) == EXPECTED_SURVIVORS
    print(f"launches={len(recs)} surviving_brokers=0 surviving={folded['surviving']}")


def test_b12_a_bound_harvest_is_reused_and_a_deleted_one_is_re_dispatched(bed, shim):
    env = _shim_env(shim, REPLY_FORMS)
    run(bed, "--dispatch", "--run-id", "d2", "--particle-config", "he.local.json",
        "--passes", "1", "--node", shim["node"], env=env, expect=0)
    first = len(_records(shim))
    assert first == 3

    _rc, out, _e = run(bed, "--dispatch", "--run-id", "d2",
                       "--particle-config", "he.local.json",
                       "--passes", "1", "--node", shim["node"], env=env, expect=0)
    assert out["reused"] == 3 and out["dispatched"] == 0
    assert len(_records(shim)) == first, "a reused slot must not re-launch"

    nd = load_module(bed)
    nd.harvest_path("d2", "seg02", 1).unlink()
    _rc, out, _e = run(bed, "--dispatch", "--run-id", "d2",
                       "--particle-config", "he.local.json",
                       "--passes", "1", "--node", shim["node"], env=env, expect=0)
    assert out["reused"] == 2 and out["dispatched"] == 1
    assert len(_records(shim)) == first + 1


@pytest.mark.parametrize("mangle,needle", [
    ("launch-fails", "codex launch returned"),
    ("no-jobid", "printed no jobId"),
    ("bad-json", "not one JSON object"),
])
def test_b20_a_failed_slot_is_counted_and_writes_no_harvest(bed, shim, mangle, needle):
    rc, out, err = run(bed, "--dispatch", "--run-id", "d3",
                       "--particle-config", "he.local.json",
                       "--passes", "1", "--max-parallel", "1",
                       "--node", shim["node"],
                       env=_shim_env(shim, REPLY_FORMS, mangle), expect=1)
    assert out["failed"] == 3 and out["dispatched"] == 0
    assert needle in err
    harvest = bed / "runs" / "name-discovery" / "d3" / "harvest"
    assert not harvest.exists() or not list(harvest.glob("*.json")), (
        "a failed slot must write no harvest")
    # ... and the fold then refuses, so a failed dispatch cannot be folded past.
    run(bed, "--fold", "--run-id", "d3", "--particle-config", "he.local.json", expect=2)


def test_b17_an_over_bound_reply_is_a_failed_slot_not_a_thin_one(bed, shim):
    nd = load_module(bed)
    too_long = "a" * (nd.MAX_FORM_CHARS + 1)
    rc, out, err = run(bed, "--dispatch", "--run-id", "d4",
                       "--particle-config", "he.local.json",
                       "--passes", "1", "--max-parallel", "1",
                       "--node", shim["node"],
                       env=_shim_env(shim, [too_long]), expect=1)
    assert out["failed"] == 3 and out["dispatched"] == 0
    assert "over the" in err
    harvest = bed / "runs" / "name-discovery" / "d4" / "harvest"
    assert not harvest.exists() or not list(harvest.glob("*.json"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
