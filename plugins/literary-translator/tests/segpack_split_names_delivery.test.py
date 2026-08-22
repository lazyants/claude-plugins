"""tests/segpack_split_names_delivery.test.py -- issue #488: a homonym split
adjudicated in canon_senses.json must reach the translate/review consumer.

`canon_senses.json` is this plugin's ONLY sanctioned answer to a source form
that denotes two or more distinct referents. The split is authorable,
schema-validated (`canon-senses.schema.json`), procedurally checked
(`canon_senses.load_senses`), evidence-verifiable (`evidence_verify.
verify_senses`) and hashed into the skeptic-side tamper stamps -- but before
this fix `segpack.py` read the sidecar ZERO times, so the one artifact the
translator and reviewer actually open never mentioned it.

The observable consequence, measured on the live `historiettes-fr-ru/tome1`
before the fix: `Notre-Dame` (split three ways -- the cathedral, the island,
the Virgin) appeared in `new_names` in seg17, seg27 and seg49, and in neither
`canon_names` nor `canon_map`. And it could not appear in `canon_names`: a
split source form is refused a bare `canon.json` entry as a `recollapse`
(`canon_validate.py`'s merge guard), and any bare entry that predates the
split is halted as a `collapsed_split` by the mandatory pre-W3a audit. So the
`entry is None` fall-through in `build_pack()`'s canon-injection loop caught
every split form and filed it as an unresolved NEW name -- the exact opposite
of "adjudicated".

This suite locks down:
  1. `build_pack()` emits `split_names` (source_form -> the sidecar's senses,
     each as sense_id/disambiguator/index_scope) for a split form found in
     the segment.
  2. THE NEGATIVE CONTROL the issue names: that same form appears in NEITHER
     `new_names` NOR `canon_names` NOR `canon_map` -- while still appearing in
     `names`, because it IS a candidate the extractor found. Removing the
     wrong constraint is only half the fix; the pre-fix behaviour would pass
     any test that merely checked the wrong mapping was absent.
  3. `validate_segpack()` enforces `split_names`' shape, and its domain is
     exactly what `canon-senses.schema.json` accepts -- notably a
     `disambiguator` must be a STRING, not a non-empty string, because the
     sidecar schema constrains only `sense_id` with `"pattern": "\\S"`. A
     stricter downstream domain would halt W3a on a sidecar the frozen source
     contract accepts.
  4. `main()` really passes the loaded sidecar (the `senses=None` default
     cannot silently reappear on the production path), and a MALFORMED
     sidecar is FATAL rather than a silent "no splits".
  5. The four prompt sites that tell a consumer what the field means.
  6. Characterization, not a fix: `used_terms_hash` does not move when a form
     migrates from `new_names` to `split_names`.
  7. Characterization, not a fix: a candidate long enough to be CAPPED by
     `bootstrap_names._capped_candidate_name` does not match its own full
     sidecar key -- exactly as it already fails to match its own `canon.json`
     key on the very next line. Pinned so the shared limitation is a recorded
     decision rather than an accident.

Loads the real, shipped `segpack.py` via importlib (mirrors tests/
segpack_verse_mount.test.py's own `_load_module` helper -- segpack.py's
`from bootstrap_names import ...` only resolves via sys.path[0] under a real
`python3 segpack.py` invocation, so its own scripts/ directory must be
inserted onto sys.path around the in-process load).
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
ASSETS_DIR = SKILL_ROOT / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SCHEMAS_DIR = ASSETS_DIR / "schemas"
TEMPLATES_DIR = ASSETS_DIR / "templates"
LANGUAGES_DIR = ASSETS_DIR / "languages"
SEGPACK_SCRIPT = SCRIPTS_DIR / "segpack.py"
CANON_SENSES_SCRIPT = SCRIPTS_DIR / "canon_senses.py"
BOOTSTRAP_NAMES_SCRIPT = SCRIPTS_DIR / "bootstrap_names.py"
CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"
SEGPACK_SCHEMA = SCHEMAS_DIR / "segpack.schema.json"
CANON_SENSES_SCHEMA = SCHEMAS_DIR / "canon-senses.schema.json"
TRANSLATE_TASK_TEMPLATE = TEMPLATES_DIR / "translate_TASK.template.md"
REVIEW_TASK_TEMPLATE = TEMPLATES_DIR / "review_TASK.template.md"
MASS_TRANSLATE_TEMPLATE = TEMPLATES_DIR / "mass-translate-wf.template.js"
STYLE_BIBLE_TEMPLATE = TEMPLATES_DIR / "style_bible.template.md"

for _required in (
    SEGPACK_SCRIPT, CANON_SENSES_SCRIPT, BOOTSTRAP_NAMES_SCRIPT, CACHE_KEY_SCRIPT,
    SEGPACK_SCHEMA, CANON_SENSES_SCHEMA, TRANSLATE_TASK_TEMPLATE, REVIEW_TASK_TEMPLATE,
    MASS_TRANSLATE_TEMPLATE, STYLE_BIBLE_TEMPLATE, LANGUAGES_DIR / "fr.json",
):
    assert _required.is_file(), f"expected shipped file at {_required}"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors segpack_verse_mount.test.py's own loader exactly (see that
    file's docstring for why the sys.path dance is needed)."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


SEGPACK_MODULE = _load_module("segpack_split_names_under_test", SEGPACK_SCRIPT, SCRIPTS_DIR)
CANON_SENSES_MODULE = _load_module(
    "canon_senses_for_split_names_test", CANON_SENSES_SCRIPT, SCRIPTS_DIR
)
BOOTSTRAP_MODULE = _load_module(
    "bootstrap_names_for_split_names_test", BOOTSTRAP_NAMES_SCRIPT, SCRIPTS_DIR
)
CACHE_KEY_MODULE = _load_module(
    "cache_key_for_split_names_test", CACHE_KEY_SCRIPT, SCRIPTS_DIR
)

# Real shipped particle config -- build_pack()'s name-scanning pass needs a
# genuinely valid LanguageConfig (never hand-rolled JSON here).
LANG_CONFIG = SEGPACK_MODULE.load_language_config("fr.json", LANGUAGES_DIR)

SPLIT_FORM = "Notre-Dame"
PLAIN_FORM = "Cosette Fantine"


# ---------------------------------------------------------------------------
# Fixtures. The sidecar is always written to disk and read back through the
# REAL canon_senses.load_senses -- never a hand-built SensesResult, so this
# suite cannot pass against a shape the shipped loader would reject.
# ---------------------------------------------------------------------------


def _base_generation_hashes():
    return {"source_extraction_hash": "a" * 40, "source_input_hash": "b" * 40}


def _canon_generation_hashes():
    return {"particle_config_hash": "c" * 40, "derivation_bundle_hash": "d" * 40}


def _evidence(block, seg):
    """The MINIMAL VERIFIABLE SET canon-senses.schema.json requires. The
    sha256 is never checked by load_senses (that is evidence_verify.py's job),
    so a well-formed placeholder digest is the honest fixture here."""
    return {
        "block": block,
        "seg": seg,
        "char_start": 0,
        "char_end": len(SPLIT_FORM),
        "context_start": 0,
        "context_end": 64,
        "sha256": "0" * 64,
    }


def _senses_doc(source_form=SPLIT_FORM, disambiguators=("the cathedral", "the Virgin")):
    return {
        "schema_version": 1,
        "entries_by_source_form": {
            source_form: {
                "senses": [
                    {
                        "sense_id": "cathedral",
                        "disambiguator": disambiguators[0],
                        "index_scope": "narrative",
                        "evidence": _evidence("PARA:seg01:0001", "seg01"),
                    },
                    {
                        "sense_id": "virgin",
                        "disambiguator": disambiguators[1],
                        "index_scope": "allusion",
                        "evidence": _evidence("PARA:seg01:0002", "seg01"),
                    },
                ]
            }
        },
    }


def _write_senses(tmp_path, doc):
    path = tmp_path / "canon_senses.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _load_senses(tmp_path, doc=None):
    path = _write_senses(tmp_path, doc if doc is not None else _senses_doc())
    return CANON_SENSES_MODULE.load_senses(
        path, allow_absent=False, schema_path=CANON_SENSES_SCHEMA
    )


def _manifest_with_split_and_plain_name():
    """`Notre-Dame` is single-token, so it is promoted into strong_names only
    via the MID-SENTENCE signal -- it is placed mid-sentence deliberately.
    `Cosette Fantine` is multiword and needs no such placement. Both are
    checked against the REAL fr.json extractor by the vacuity guard below,
    never assumed."""
    return {
        "segments": [
            {
                "seg": "seg01",
                "title_text": "Chapter One",
                "kind": "body",
                "word_count": 20,
                "block_ids": ["p1"],
            }
        ],
        "blocks": {
            "p1": {
                "id": "p1",
                "order_index": 0,
                "plain_text": (
                    "Il entra dans Notre-Dame le matin. "
                    "Cosette Fantine jouait non loin de Notre-Dame."
                ),
            },
        },
        "footnotes": [],
        "verse": {"store": []},
        "generation_hashes": _base_generation_hashes(),
    }


def _canon_without_the_split_form():
    """A split source_form has no bare canon.json entry -- the merge guard
    refuses to create one (recollapse) and the pre-W3a audit halts on any that
    predates the split. `Cosette Fantine` is likewise uncanonized, so it stays
    the suite's live proof that new_names still works."""
    return {"entries": {}, "generation_hashes": _canon_generation_hashes()}


def _build(tmp_path, manifest=None, canon=None, senses_doc=None):
    return SEGPACK_MODULE.build_pack(
        "seg01",
        manifest if manifest is not None else _manifest_with_split_and_plain_name(),
        canon if canon is not None else _canon_without_the_split_form(),
        LANG_CONFIG,
        "omit_apparatus",
        _load_senses(tmp_path, senses_doc),
    )


# ---------------------------------------------------------------------------
# 1. Vacuity guard. If the extractor stops finding these candidates, every
#    assertion below becomes trivially true -- so prove they are found FIRST.
# ---------------------------------------------------------------------------


def test_fixture_actually_yields_both_candidate_names(tmp_path):
    pack = _build(tmp_path)
    assert SPLIT_FORM in pack["names"], (
        f"fixture no longer yields {SPLIT_FORM!r} as a candidate -- every other "
        f"assertion in this suite would pass vacuously. names={pack['names']}"
    )
    assert PLAIN_FORM in pack["names"], pack["names"]


# ---------------------------------------------------------------------------
# 2. build_pack() -- split_names delivery, and the negative control.
# ---------------------------------------------------------------------------


def test_build_pack_delivers_the_split_with_its_senses(tmp_path):
    pack = _build(tmp_path)
    assert pack["split_names"] == {
        SPLIT_FORM: [
            {
                "sense_id": "cathedral",
                "disambiguator": "the cathedral",
                "index_scope": "narrative",
            },
            {
                "sense_id": "virgin",
                "disambiguator": "the Virgin",
                "index_scope": "allusion",
            },
        ]
    }, pack["split_names"]


def test_build_pack_preserves_sidecar_sense_order(tmp_path):
    """The sidecar's senses[] order is the operator's own ordering; it is
    carried through verbatim rather than sorted, so the disambiguators a
    consumer reads appear in the order they were adjudicated."""
    pack = _build(tmp_path)
    assert [s["sense_id"] for s in pack["split_names"][SPLIT_FORM]] == ["cathedral", "virgin"]


def test_build_pack_keeps_the_split_form_out_of_new_names(tmp_path):
    """THE negative control #488 names. Before the fix this was the ONLY
    place the split form could land."""
    pack = _build(tmp_path)
    assert SPLIT_FORM not in pack["new_names"], (
        f"{SPLIT_FORM!r} is an adjudicated homonym split, not an unresolved "
        f"new name; new_names={pack['new_names']}"
    )
    assert PLAIN_FORM in pack["new_names"], (
        f"a genuinely uncanonized name must still reach new_names -- otherwise "
        f"this suite would pass on a build_pack() that emptied it. "
        f"new_names={pack['new_names']}"
    )


def test_build_pack_keeps_the_split_form_out_of_canon_names_and_canon_map(tmp_path):
    pack = _build(tmp_path)
    assert SPLIT_FORM not in pack["canon_names"], pack["canon_names"]
    assert SPLIT_FORM not in pack["canon_map"], pack["canon_map"]


def test_build_pack_split_form_still_appears_in_names(tmp_path):
    """`names` is the extractor's candidate list, not a canon verdict -- a
    split form is still a candidate that was found in this segment."""
    pack = _build(tmp_path)
    assert SPLIT_FORM in pack["names"], pack["names"]


def test_build_pack_split_names_keys_are_disjoint_from_the_other_two_lists(tmp_path):
    pack = _build(tmp_path)
    assert set(pack["split_names"]) & set(pack["canon_names"]) == set()
    assert set(pack["split_names"]) & set(pack["new_names"]) == set()
    assert set(pack["split_names"]) <= set(pack["names"])


def test_build_pack_matches_the_split_form_through_normalize_form(tmp_path):
    """The sidecar key is compared via canon_senses.normalize_form (NFC +
    casefold + whitespace collapse), never by raw equality -- a sidecar
    spelled with a different case must still be recognised."""
    doc = _senses_doc(source_form="notre-dame")
    pack = _build(tmp_path, senses_doc=doc)
    assert SPLIT_FORM in pack["split_names"], pack["split_names"]
    assert SPLIT_FORM not in pack["new_names"], pack["new_names"]


def test_build_pack_with_an_empty_sidecar_changes_nothing(tmp_path):
    doc = {"schema_version": 1, "entries_by_source_form": {}}
    pack = _build(tmp_path, senses_doc=doc)
    assert pack["split_names"] == {}
    assert SPLIT_FORM in pack["new_names"], pack["new_names"]


def test_build_pack_without_a_sidecar_argument_reproduces_the_old_shape():
    """`senses=None` is the explicit no-sidecar state, and must still emit the
    field (always present, possibly empty) rather than omitting it."""
    pack = SEGPACK_MODULE.build_pack(
        "seg01",
        _manifest_with_split_and_plain_name(),
        _canon_without_the_split_form(),
        LANG_CONFIG,
        "omit_apparatus",
    )
    assert pack["split_names"] == {}
    assert SPLIT_FORM in pack["new_names"], pack["new_names"]


def test_build_pack_accepts_an_empty_disambiguator(tmp_path):
    """canon-senses.schema.json constrains `disambiguator` to `"type":
    "string"` only -- `sense_id` is the field carrying `"pattern": "\\S"`.
    An empty disambiguator is therefore a loadable sidecar value, and segpack
    must not invent a stricter domain than the contract it consumes."""
    doc = _senses_doc(disambiguators=("", "the Virgin"))
    pack = _build(tmp_path, senses_doc=doc)
    assert pack["split_names"][SPLIT_FORM][0]["disambiguator"] == ""
    assert SEGPACK_MODULE.validate_segpack(pack, "seg01") == []


# ---------------------------------------------------------------------------
# 3. validate_segpack() -- split_names shape enforcement.
# ---------------------------------------------------------------------------


def _pack_with_split_names(split_names=None, names=None, canon_names=(), new_names=()):
    if split_names is None:
        split_names = {
            SPLIT_FORM: [
                {"sense_id": "cathedral", "disambiguator": "the cathedral",
                 "index_scope": "narrative"},
                {"sense_id": "virgin", "disambiguator": "the Virgin",
                 "index_scope": "allusion"},
            ]
        }
    if names is None:
        names = sorted(set(split_names) | set(canon_names) | set(new_names))
    return {
        "seg": "seg01",
        "title": "Chapter One",
        "kind": "body",
        "word_count": 4,
        "blocks": [],
        "footnotes": [],
        "verses": [],
        "names": list(names),
        "canon_names": list(canon_names),
        "new_names": list(new_names),
        "canon_map": {},
        "split_names": split_names,
        "generation_hashes": {
            "source_extraction_hash": "a" * 40,
            "source_input_hash": "b" * 40,
            "particle_config_hash": "c" * 40,
            "derivation_bundle_hash": "d" * 40,
        },
    }


def test_validate_segpack_accepts_well_formed_split_names():
    assert SEGPACK_MODULE.validate_segpack(_pack_with_split_names()) == []


def test_validate_segpack_accepts_empty_split_names():
    assert SEGPACK_MODULE.validate_segpack(_pack_with_split_names(split_names={})) == []


def test_validate_segpack_rejects_missing_split_names_field():
    pack = _pack_with_split_names()
    del pack["split_names"]
    errors = SEGPACK_MODULE.validate_segpack(pack)
    assert any("missing required top-level field" in e and "split_names" in e for e in errors), errors


def test_validate_segpack_rejects_non_dict_split_names():
    errors = SEGPACK_MODULE.validate_segpack(_pack_with_split_names(split_names=[]))
    assert any("'split_names' must be an object" in e for e in errors), errors


@pytest.mark.parametrize("bad_key", ["", 7])
def test_validate_segpack_rejects_bad_split_names_key(bad_key):
    pack = _pack_with_split_names(
        split_names={bad_key: [
            {"sense_id": "a", "disambiguator": "x", "index_scope": "narrative"},
            {"sense_id": "b", "disambiguator": "y", "index_scope": "narrative"},
        ]},
        names=[SPLIT_FORM],
    )
    errors = SEGPACK_MODULE.validate_segpack(pack)
    assert any("'split_names' has a non-string/empty key" in e for e in errors), errors


def test_validate_segpack_rejects_split_names_key_not_in_names():
    pack = _pack_with_split_names(names=["Someone Else"])
    errors = SEGPACK_MODULE.validate_segpack(pack)
    assert any("is not in 'names'" in e for e in errors), errors


def test_validate_segpack_rejects_split_names_key_also_in_new_names():
    pack = _pack_with_split_names(new_names=(SPLIT_FORM,), names=[SPLIT_FORM])
    errors = SEGPACK_MODULE.validate_segpack(pack)
    assert any("must not also appear in" in e for e in errors), errors


def test_validate_segpack_rejects_split_names_key_also_in_canon_names():
    pack = _pack_with_split_names(canon_names=(SPLIT_FORM,), names=[SPLIT_FORM])
    errors = SEGPACK_MODULE.validate_segpack(pack)
    assert any("must not also appear in" in e for e in errors), errors


def test_validate_segpack_rejects_fewer_than_two_senses():
    """A split is >=2 senses -- canon-senses.schema.json enforces `minItems:
    2` on the source, and a one-sense segpack entry means something dropped a
    sense between the loader and here."""
    pack = _pack_with_split_names(split_names={
        SPLIT_FORM: [{"sense_id": "a", "disambiguator": "x", "index_scope": "narrative"}]
    })
    errors = SEGPACK_MODULE.validate_segpack(pack)
    assert any("at least 2 senses" in e for e in errors), errors


def test_validate_segpack_rejects_non_list_sense_value():
    pack = _pack_with_split_names(split_names={SPLIT_FORM: {"sense_id": "a"}})
    errors = SEGPACK_MODULE.validate_segpack(pack)
    assert any("must be an array" in e and "split_names" in e for e in errors), errors


def test_validate_segpack_rejects_empty_sense_id():
    pack = _pack_with_split_names(split_names={
        SPLIT_FORM: [
            {"sense_id": "", "disambiguator": "x", "index_scope": "narrative"},
            {"sense_id": "b", "disambiguator": "y", "index_scope": "narrative"},
        ]
    })
    errors = SEGPACK_MODULE.validate_segpack(pack)
    assert any("'sense_id'" in e for e in errors), errors


def test_validate_segpack_rejects_duplicate_sense_id():
    pack = _pack_with_split_names(split_names={
        SPLIT_FORM: [
            {"sense_id": "same", "disambiguator": "x", "index_scope": "narrative"},
            {"sense_id": "same", "disambiguator": "y", "index_scope": "narrative"},
        ]
    })
    errors = SEGPACK_MODULE.validate_segpack(pack)
    assert any("duplicate 'sense_id'" in e for e in errors), errors


def test_validate_segpack_rejects_non_string_disambiguator():
    pack = _pack_with_split_names(split_names={
        SPLIT_FORM: [
            {"sense_id": "a", "disambiguator": None, "index_scope": "narrative"},
            {"sense_id": "b", "disambiguator": "y", "index_scope": "narrative"},
        ]
    })
    errors = SEGPACK_MODULE.validate_segpack(pack)
    assert any("'disambiguator'" in e for e in errors), errors


def test_validate_segpack_rejects_unknown_index_scope():
    pack = _pack_with_split_names(split_names={
        SPLIT_FORM: [
            {"sense_id": "a", "disambiguator": "x", "index_scope": "invented"},
            {"sense_id": "b", "disambiguator": "y", "index_scope": "narrative"},
        ]
    })
    errors = SEGPACK_MODULE.validate_segpack(pack)
    assert any("'index_scope'" in e for e in errors), errors


def test_validate_segpack_rejects_extra_sense_field():
    pack = _pack_with_split_names(split_names={
        SPLIT_FORM: [
            {"sense_id": "a", "disambiguator": "x", "index_scope": "narrative",
             "evidence": {}},
            {"sense_id": "b", "disambiguator": "y", "index_scope": "narrative"},
        ]
    })
    errors = SEGPACK_MODULE.validate_segpack(pack)
    assert any("unexpected field" in e for e in errors), errors


# ---------------------------------------------------------------------------
# 4. Schema / hand-rolled-validator parity.
# ---------------------------------------------------------------------------


def test_schema_declares_split_names_required():
    schema = json.loads(SEGPACK_SCHEMA.read_text(encoding="utf-8"))
    assert "split_names" in schema["properties"], sorted(schema["properties"])
    assert "split_names" in schema["required"], schema["required"]


def test_schema_required_matches_the_hand_rolled_top_level_key_set():
    schema = json.loads(SEGPACK_SCHEMA.read_text(encoding="utf-8"))
    assert set(schema["required"]) == set(SEGPACK_MODULE._TOP_LEVEL_KEYS)
    assert set(schema["properties"]) == set(SEGPACK_MODULE._TOP_LEVEL_KEYS)


def test_schema_sense_object_does_not_admit_a_target_form():
    """No per-sense target form exists anywhere in the sidecar contract, so
    the segpack must not invent one -- a reviewer choosing a sense does it by
    disambiguator, and there is no frozen canon to quote."""
    schema = json.loads(SEGPACK_SCHEMA.read_text(encoding="utf-8"))
    sense = schema["properties"]["split_names"]["additionalProperties"]["items"]
    assert sense["additionalProperties"] is False
    assert set(sense["required"]) == {"sense_id", "disambiguator", "index_scope"}
    assert "canonical_target_form" not in sense["properties"]


def test_schema_sense_index_scope_enum_matches_the_sidecar_contract():
    segpack_schema = json.loads(SEGPACK_SCHEMA.read_text(encoding="utf-8"))
    senses_schema = json.loads(CANON_SENSES_SCHEMA.read_text(encoding="utf-8"))
    theirs = (
        senses_schema["properties"]["entries_by_source_form"]["additionalProperties"]
        ["properties"]["senses"]["items"]["properties"]["index_scope"]["enum"]
    )
    mine = (
        segpack_schema["properties"]["split_names"]["additionalProperties"]
        ["items"]["properties"]["index_scope"]["enum"]
    )
    assert mine == theirs, (mine, theirs)


def test_schema_sense_disambiguator_is_not_stricter_than_the_sidecar():
    """The one place a downstream domain could halt a valid project: the
    sidecar accepts an empty disambiguator, so this schema must too."""
    schema = json.loads(SEGPACK_SCHEMA.read_text(encoding="utf-8"))
    disambiguator = (
        schema["properties"]["split_names"]["additionalProperties"]
        ["items"]["properties"]["disambiguator"]
    )
    assert disambiguator == {"type": "string"} or (
        disambiguator.get("type") == "string"
        and "minLength" not in disambiguator
        and "pattern" not in disambiguator
    ), disambiguator


# ---------------------------------------------------------------------------
# 5. main() -- the production path really loads the sidecar, and a malformed
#    one is FATAL rather than a silent "no splits".
# ---------------------------------------------------------------------------


def _scaffold_durable_root(tmp_path, senses_bytes):
    """A minimal ${durable_root} of the shape Step 0a produces: segpack.py is
    self-anchored at ${durable_root}/scripts/, so the whole run has to happen
    against a real directory layout, never an in-process call."""
    root = tmp_path / "root"
    (root / "scripts").mkdir(parents=True)
    (root / "schemas").mkdir()
    (root / "languages").mkdir()
    for script in (SEGPACK_SCRIPT, BOOTSTRAP_NAMES_SCRIPT, CANON_SENSES_SCRIPT):
        shutil.copy2(script, root / "scripts" / script.name)
    shutil.copy2(CANON_SENSES_SCHEMA, root / "schemas" / CANON_SENSES_SCHEMA.name)
    shutil.copy2(LANGUAGES_DIR / "fr.json", root / "languages" / "fr.json")
    (root / "manifest.json").write_text(
        json.dumps(_manifest_with_split_and_plain_name(), ensure_ascii=False), encoding="utf-8"
    )
    (root / "canon.json").write_text(
        json.dumps(_canon_without_the_split_form(), ensure_ascii=False), encoding="utf-8"
    )
    if senses_bytes is not None:
        (root / "canon_senses.json").write_bytes(senses_bytes)
    return root


def _run_segpack(root):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "segpack.py"), "seg01",
         "--particle-config", "fr.json", "--apparatus-policy", "omit_apparatus"],
        capture_output=True, text=True,
    )


def test_main_passes_the_sidecar_to_build_pack(tmp_path):
    """Guards the one way the `senses=None` default could silently reappear
    in production: main() forgetting to load and pass it."""
    root = _scaffold_durable_root(
        tmp_path, json.dumps(_senses_doc(), ensure_ascii=False).encode("utf-8")
    )
    proc = _run_segpack(root)
    assert proc.returncode == 0, proc.stderr
    pack = json.loads((root / "segments" / "segpack_seg01.json").read_text(encoding="utf-8"))
    assert SPLIT_FORM in pack["split_names"], pack["split_names"]
    assert SPLIT_FORM not in pack["new_names"], pack["new_names"]


def test_main_without_a_sidecar_is_not_an_error(tmp_path):
    """An absent canon_senses.json is the ordinary state of a project with no
    adjudicated homonym -- it must not be a preflight failure."""
    root = _scaffold_durable_root(tmp_path, None)
    proc = _run_segpack(root)
    assert proc.returncode == 0, proc.stderr
    pack = json.loads((root / "segments" / "segpack_seg01.json").read_text(encoding="utf-8"))
    assert pack["split_names"] == {}
    assert SPLIT_FORM in pack["new_names"], pack["new_names"]


def test_main_treats_a_malformed_sidecar_as_fatal(tmp_path):
    """A schema-invalid sidecar must HALT, never degrade to "no splits" -- the
    degraded path would ship the exact defect #488 exists to close, silently
    and green."""
    root = _scaffold_durable_root(tmp_path, b'{"schema_version": 1}')
    proc = _run_segpack(root)
    assert proc.returncode != 0, proc.stdout
    assert "canon_senses" in (proc.stderr + proc.stdout)
    assert not (root / "segments" / "segpack_seg01.json").exists()


def test_main_treats_an_unreadable_sidecar_as_fatal(tmp_path):
    """A directory where the sidecar belongs is a BLOCK in load_senses
    regardless of allow_absent -- it must not read as "absent"."""
    root = _scaffold_durable_root(tmp_path, None)
    (root / "canon_senses.json").mkdir()
    proc = _run_segpack(root)
    assert proc.returncode != 0, proc.stdout


# ---------------------------------------------------------------------------
# 6. CHARACTERIZATION, not a fix: used_terms_hash does not move.
#
#    This assertion passes on the UNMODIFIED implementation too -- it is a
#    recorded cost claim, not a RED-before-green delivery test. What it pins
#    is that the two packs differ in MEMBERSHIP while the canon projection
#    compute_used_terms_hash actually reads stays identical, which is what
#    makes "no per-segment staleness" true rather than merely asserted.
# ---------------------------------------------------------------------------


def test_used_terms_hash_is_unchanged_when_a_form_moves_to_split_names(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "canon.json").write_text(
        json.dumps(_canon_without_the_split_form(), ensure_ascii=False), encoding="utf-8"
    )

    before = _pack_with_split_names(
        split_names={}, new_names=(SPLIT_FORM, PLAIN_FORM), names=[SPLIT_FORM, PLAIN_FORM]
    )
    after = _pack_with_split_names(new_names=(PLAIN_FORM,), names=[SPLIT_FORM, PLAIN_FORM])

    assert set(before["new_names"]) != set(after["new_names"]), "fixture proves nothing"
    assert set(before["split_names"]) != set(after["split_names"]), "fixture proves nothing"

    assert CACHE_KEY_MODULE.compute_used_terms_hash(root, before) == \
        CACHE_KEY_MODULE.compute_used_terms_hash(root, after)


# ---------------------------------------------------------------------------
# 7. CHARACTERIZATION, not a fix: a CAPPED candidate matches neither its
#    sidecar key nor its canon key.
#
#    `_capped_candidate_name` replaces an over-long candidate with a prefix
#    plus a digest marker, and that marker-bearing string is the identity
#    every lookup in build_pack()'s canon-injection loop uses -- the sidecar
#    lookup added by #488 and the pre-existing `canon_entries.get(name)`
#    alike. Neither can reconstruct the truncated suffix. Teaching only the
#    sidecar lookup to match a capped representation would make it strictly
#    more capable than the canon lookup beside it, which is the half-fix
#    shape #383 already records; so the shared blindness is pinned here as a
#    decision rather than left to be rediscovered. Measured population at the
#    time of writing: 0 of 3884 candidate names in the live tome1 corpus
#    exceed the cap (longest: 62 characters).
# ---------------------------------------------------------------------------


def test_a_capped_candidate_does_not_match_its_full_sidecar_key(tmp_path):
    # Letters only -- the extractor's tokenizer drops digits, so a
    # digit-suffixed fixture would collapse into one repeated word and the
    # emitted candidate would not be the string this test thinks it is.
    long_form = " ".join(
        "Beaumont" + chr(ord("a") + i // 26) + chr(ord("a") + i % 26) for i in range(40)
    )
    capped = BOOTSTRAP_MODULE._capped_candidate_name(long_form)
    assert capped != long_form, (
        "fixture no longer exceeds the candidate-name cap, so this "
        "characterization would pass vacuously"
    )

    manifest = _manifest_with_split_and_plain_name()
    manifest["blocks"]["p1"]["plain_text"] = f"Il rencontra {long_form} le matin."

    doc = _senses_doc(source_form=long_form)
    pack = _build(tmp_path, manifest=manifest, senses_doc=doc)

    assert capped in pack["names"], (
        "fixture must reach build_pack as the CAPPED emitted string -- "
        f"got {pack['names']}"
    )
    assert capped not in pack["split_names"], pack["split_names"]
    assert capped in pack["new_names"], pack["new_names"]
    assert SEGPACK_MODULE.validate_segpack(pack, "seg01") == []


# ---------------------------------------------------------------------------
# 8. Prompt-contract prose: the four sites that tell a consumer what the
#    field means. A field nothing explains is not delivered.
# ---------------------------------------------------------------------------


def test_translate_task_template_explains_split_names():
    text = TRANSLATE_TASK_TEMPLATE.read_text(encoding="utf-8")
    assert "split_names" in text, (
        "translate_TASK.template.md must tell the translator what a "
        "split_names entry is -- otherwise the field arrives unexplained"
    )
    assert "disambiguator" in text


def test_review_task_template_explains_split_names():
    text = REVIEW_TASK_TEMPLATE.read_text(encoding="utf-8")
    assert "split_names" in text, (
        "review_TASK.template.md must tell the reviewer that a split_names "
        "form carries no frozen canon, so it is neither an uncanonized name "
        "nor a form whose canonical target may be prescribed"
    )
    assert "disambiguator" in text


def test_live_translate_prompt_explains_split_names():
    """The one-time task files are copied ONCE, but this prompt is generated
    fresh on every W5 run -- so an existing project's translator learns about
    split_names here or nowhere."""
    text = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    translate_prompt = text.split("function translatePrompt(")[1].split("\nfunction ")[0]
    assert "split_names" in translate_prompt, (
        "translatePrompt() enumerates the segpack fields the translator must "
        "act on; split_names must be among them"
    )


def test_live_review_and_fix_prompts_explain_split_names():
    text = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    for fn in ("reviewDispatchPrompt(", "fixPrompt("):
        body = text.split("function " + fn)[1].split("\nfunction ")[0]
        assert "split_names" in body, (
            f"{fn}) tells the agent that a form resolving in neither canon_map "
            f"nor canon.json is not canon -- which is exactly what a split "
            f"form does, so the split case must be named there"
        )


def test_style_bible_template_lists_split_names():
    text = STYLE_BIBLE_TEMPLATE.read_text(encoding="utf-8")
    assert "split_names" in text, (
        "style_bible.template.md enumerates what segpack.py injects into "
        "every segment; that list is now incomplete without split_names"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
