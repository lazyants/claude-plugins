"""tests/ledger_composite_key.test.py -- regression-lock suite for
scripts/cache_key.py, the ONE shared implementation of the literary-translator
plugin's 15-field composite ``cache_key``.

Authoritative spec: skills/literary-translator/references/ledger-and-resumability.md,
the "Composite cache key -- exact 15-field JSON structure", "Exact byte-scope
per field", and "The three separate bundle hashes -- exact membership"
sections. Every assertion below traces back to a sentence in that document.

Each test builds a REAL, self-contained ``durable_root`` fixture on disk
(ownership marker, profile.yml, style_bible.md, schemas/, prompt files,
languages/<particle_config>, extract.py, manifest.json, canon.json,
segments/segpack_{seg}.json, scripts/{bootstrap_names,segpack}.py,
runs/.plugin_bundle_hash) and copies the REAL cache_key.py into
``{root}/scripts/cache_key.py`` -- exactly the way it is actually invoked in
production (``python3 {durable_root}/scripts/cache_key.py --seg ...``) -- so
its ``Path(__file__).resolve().parents[1]`` self-anchoring resolves against
the isolated fixture root rather than this repo's real assets/scripts
directory. Every hash is invoked as a real subprocess and its output
compared against an EXPECTED value the test derives independently (never by
importing/calling the script's own compute_* functions), so a test failure
here means the script's real behavior diverged from the spec, not that the
test re-asserts whatever the code already does.

Coverage (per the test's own enumeration):
  - one test per each of the 15 exact cache_key fields, each proving the
    EXACT byte-scope (what's included, and -- just as important -- what's
    deliberately excluded/ignored);
  - the two asymmetric used_terms_hash cases (an uncanonized new_names[]
    entry contributes nothing; canonizing it elsewhere later flips the
    segment stale with no persisted reverse index);
  - the dedicated negative case proving engine.batch_agent_cap is EXCLUDED
    from agent_config_hash (and from the full 15-field key) -- changing it
    alone must never invalidate a converged segment.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SRC = (
    PLUGIN_ROOT
    / "skills"
    / "literary-translator"
    / "assets"
    / "scripts"
    / "cache_key.py"
)

assert SCRIPT_SRC.is_file(), f"cache_key.py not found at {SCRIPT_SRC}"

# Field order copied verbatim from the authoritative doc / the script's own
# CACHE_KEY_FIELD_ORDER -- used only to compute "which of the 15 fields
# changed" diffs below, never to drive the fixtures.
CACHE_KEY_FIELD_ORDER = (
    "input_sha1",
    "style_contract_hash",
    "used_terms_hash",
    "pipeline_version",
    "schema_hash",
    "prompt_hash",
    "agent_config_hash",
    "profile_semantics_hash",
    "particle_config_hash",
    "source_extraction_hash",
    "source_input_hash",
    "derivation_bundle_hash",
    "verse_map_hash",
    "note_map_hash",
    "plugin_bundle_hash",
)

BEGIN_MARKER = b"<!-- STYLE_CONTRACT_BEGIN -->"
END_MARKER = b"<!-- STYLE_CONTRACT_END -->"
STYLE_BIBLE_PREAMBLE = b"# Style Bible\n\nPreamble text outside the contract.\n\n"
STYLE_BIBLE_DEFAULT_INSIDE = (
    b"## A. Tone\nFormal but warm, second person avoided.\n"
    b"## F. Punctuation\nOxford comma required.\n"
)
STYLE_BIBLE_DEFAULT_GLOSSARY = (
    b"## G. Glossary\n\n- Jean -> Zhan (locked form, do not translate literally)\n"
)


# ---------------------------------------------------------------------------
# Independent expected-value helpers (deliberately NOT imported from the
# script under test -- re-derived from the spec doc's own prose).
# ---------------------------------------------------------------------------


def canonical_json_bytes(obj) -> bytes:
    """Same canonical-JSON formula the spec names for every hashed object:
    sorted keys, compact separators, non-ASCII preserved verbatim."""
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def build_style_bible(
    inside: bytes | None = None, glossary: bytes | None = None
) -> bytes:
    inside = STYLE_BIBLE_DEFAULT_INSIDE if inside is None else inside
    glossary = STYLE_BIBLE_DEFAULT_GLOSSARY if glossary is None else glossary
    return (
        STYLE_BIBLE_PREAMBLE
        + BEGIN_MARKER
        + b"\n"
        + inside
        + END_MARKER
        + b"\n\n"
        + glossary
    )


def expected_style_contract_hash(raw: bytes) -> str:
    """Bytes strictly BETWEEN the markers (never the markers themselves) --
    the same slicing rule the spec states in prose, re-derived here via raw
    string ops rather than by calling the script."""
    begin_idx = raw.find(BEGIN_MARKER) + len(BEGIN_MARKER)
    end_idx = raw.find(END_MARKER)
    assert begin_idx >= len(BEGIN_MARKER) and end_idx >= 0
    return hashlib.sha1(raw[begin_idx:end_idx]).hexdigest()


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def default_profile() -> dict:
    return {
        "project": {"pipeline_version": "v1.2.3"},
        "engine": {"effort": "medium", "max_fix_rounds": 3, "batch_agent_cap": 1000},
        "source": {
            "format": "plain_text",
            "path": "/logical/original/path.txt",
            "language": {"code": "fr", "particle_config": "fr_particles.json"},
            "adapter_config": {
                "plain_text": {"encoding": "utf-8"},
                # Decoy sub-block for a DIFFERENT format -- must never enter
                # source_extraction_hash while source.format stays plain_text.
                "gutenberg_epub": {"strip_toc": True},
            },
        },
        "target": {"language": {"code": "ru"}},
        "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": 4},
        "footnotes": {"apparatus_policy": "translate_all"},
        "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
    }


def default_segpack(seg: str = "seg01") -> dict:
    return {
        "seg": seg,
        "blocks": [
            {"id": "b0", "order_index": 0, "source_html": "<p>Bonjour</p>"},
            {"id": "b1", "order_index": 1, "plain_text": "le monde"},
        ],
        "canon_names": ["Jean"],
        "new_names": ["Pierre"],  # deliberately NOT yet in canon.entries
        "verses": [
            {"vid": "v1", "placeholder": "⟦VERSE_v1⟧", "parent_block": "b0"},
        ],
        "footnotes": [
            {"n": 1, "source_text": "a footnote"},
        ],
    }


def default_canon() -> dict:
    return {
        "entries": {
            "Jean": {"target": "Жан", "gender": "m"},
            # "Marie" is an UNREFERENCED decoy entry -- no fixture segpack's
            # canon_names/new_names ever names her.
            "Marie": {"target": "Мари", "gender": "f"},
        },
        "review_queue": [],
    }


def write_profile(root: Path, profile: dict) -> None:
    (root / "profile.yml").write_text(
        yaml.safe_dump(profile, sort_keys=False), encoding="utf-8"
    )


def edit_profile(root: Path, mutate) -> None:
    prof_path = root / "profile.yml"
    profile = yaml.safe_load(prof_path.read_text(encoding="utf-8"))
    mutate(profile)
    prof_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")


def set_profile_value(root: Path, dotted: str, value) -> None:
    parts = dotted.split(".")

    def mutate(profile):
        cur = profile
        for key in parts[:-1]:
            cur = cur[key]
        cur[parts[-1]] = value

    edit_profile(root, mutate)


def write_canon(root: Path, canon: dict) -> None:
    (root / "canon.json").write_text(
        json.dumps(canon, ensure_ascii=False), encoding="utf-8"
    )


def write_segpack(root: Path, seg: str, segpack: dict) -> None:
    (root / "segments" / f"segpack_{seg}.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8"
    )


def write_manifest(root: Path, source_inputs, extra: dict | None = None) -> None:
    manifest = {"source_inputs": list(source_inputs)}
    if extra:
        manifest.update(extra)
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


def make_durable_root(tmp_path) -> Path:
    """Build a COMPLETE, internally-consistent durable_root -- every global
    and per-segment field's requisite file/profile-key is present -- so the
    full 15-field ``--seg`` computation succeeds unmodified. Individual tests
    then mutate exactly the one file/field relevant to what they're proving."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SCRIPT_SRC, scripts_dir / "cache_key.py")

    write_profile(root, default_profile())
    marker = {"owner_profile_path": str(root / "profile.yml")}
    (root / ".literary-translator-root.json").write_text(
        json.dumps(marker), encoding="utf-8"
    )

    (root / "style_bible.md").write_bytes(build_style_bible())

    schemas_dir = root / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "draft.schema.json").write_bytes(b'{"draft":"schema-v1"}')
    (schemas_dir / "review.schema.json").write_bytes(b'{"review":"schema-v1"}')
    (schemas_dir / "segpack.schema.json").write_bytes(b'{"segpack":"schema-v1"}')

    (root / "translate_TASK.md").write_bytes(b"TRANSLATE TASK PROMPT v1\n")
    (root / "review_TASK.md").write_bytes(b"REVIEW TASK PROMPT v1\n")

    languages_dir = root / "languages"
    languages_dir.mkdir()
    (languages_dir / "fr_particles.json").write_bytes(
        b'{"particles": ["de", "du", "des"]}'
    )

    (root / "extract.py").write_bytes(b"# extract.py fixture v1\n")

    source_file = root / "source_original.txt"
    source_file.write_bytes(b"Ceci est le texte source original.\n")
    write_manifest(root, [str(source_file.resolve())])

    (scripts_dir / "bootstrap_names.py").write_bytes(b"# bootstrap_names.py fixture v1\n")
    (scripts_dir / "segpack.py").write_bytes(b"# segpack.py fixture v1\n")

    runs_dir = root / "runs"
    runs_dir.mkdir()
    (runs_dir / ".plugin_bundle_hash").write_text(
        "baseline-marker-0000\n", encoding="utf-8"
    )

    write_canon(root, default_canon())

    (root / "segments").mkdir()
    write_segpack(root, "seg01", default_segpack())

    return root


# ---------------------------------------------------------------------------
# Invocation helpers
# ---------------------------------------------------------------------------


def run_cache_key(root: Path, args, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "cache_key.py"), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def full_key(root: Path, seg: str = "seg01") -> dict:
    proc = run_cache_key(root, ["--seg", seg])
    assert proc.returncode == 0, (
        f"cache_key.py --seg {seg} failed:\nSTDOUT:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


def one_field(root: Path, field: str, seg: str | None = None) -> str:
    args = ["--field", field] if seg is None else ["--seg", seg, "--field", field]
    proc = run_cache_key(root, args)
    assert proc.returncode == 0, (
        f"cache_key.py {' '.join(args)} failed:\nSTDOUT:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}"
    )
    return proc.stdout.rstrip("\n")


def assert_only_changed(before: dict, after: dict, expected) -> None:
    changed = {f for f in CACHE_KEY_FIELD_ORDER if before[f] != after[f]}
    assert changed == set(expected), (
        f"expected only {set(expected)} to change, got {changed}\n"
        f"before={before}\nafter={after}"
    )


# ---------------------------------------------------------------------------
# 1. input_sha1 -- concatenated source_html/plain_text of every block, in
#    order_index order (never file/array order).
# ---------------------------------------------------------------------------


def test_input_sha1_exact_scope(tmp_path):
    root = make_durable_root(tmp_path)
    seg = default_segpack()
    # blocks listed in the JSON array OUT of order_index order.
    seg["blocks"] = [
        {"id": "b1", "order_index": 1, "plain_text": "le monde"},
        {"id": "b0", "order_index": 0, "source_html": "<p>Bonjour</p>"},
    ]
    write_segpack(root, "seg01", seg)

    expected = hashlib.sha1(("<p>Bonjour</p>" + "le monde").encode("utf-8")).hexdigest()
    assert one_field(root, "input_sha1", seg="seg01") == expected

    # Reordering the blocks[] ARRAY (same order_index values) must not
    # change the hash -- concatenation order is order_index, not file order.
    seg_reordered = dict(seg)
    seg_reordered["blocks"] = list(reversed(seg["blocks"]))
    write_segpack(root, "seg01", seg_reordered)
    assert one_field(root, "input_sha1", seg="seg01") == expected

    # Changing actual block prose changes the hash.
    seg_mutated = json.loads(json.dumps(seg))
    for block in seg_mutated["blocks"]:
        if block["id"] == "b0":
            block["source_html"] = "<p>Bonsoir</p>"
    write_segpack(root, "seg01", seg_mutated)
    assert one_field(root, "input_sha1", seg="seg01") != expected


# ---------------------------------------------------------------------------
# 2. style_contract_hash -- sha1 of bytes strictly between the
#    STYLE_CONTRACT_BEGIN/END markers only; section G (glossary), outside
#    the END marker, must never affect it.
# ---------------------------------------------------------------------------


def test_style_contract_hash_exact_scope(tmp_path):
    root = make_durable_root(tmp_path)

    raw = build_style_bible()
    expected = expected_style_contract_hash(raw)
    (root / "style_bible.md").write_bytes(raw)
    assert one_field(root, "style_contract_hash") == expected

    # Bytes OUTSIDE the markers (glossary section) must not affect it.
    raw_glossary_changed = build_style_bible(
        glossary=b"## G. Glossary\n\n- Jean -> COMPLETELY DIFFERENT GLOSS\n"
    )
    (root / "style_bible.md").write_bytes(raw_glossary_changed)
    assert one_field(root, "style_contract_hash") == expected

    # Bytes INSIDE the markers must change it.
    raw_inside_changed = build_style_bible(inside=b"## A. Tone\nCasual now, slangy.\n")
    (root / "style_bible.md").write_bytes(raw_inside_changed)
    changed = one_field(root, "style_contract_hash")
    assert changed != expected
    assert changed == expected_style_contract_hash(raw_inside_changed)


# ---------------------------------------------------------------------------
# 3. used_terms_hash -- sha1 of ONLY the canon.entries currently referenced
#    by this segment's own canon_names[]/new_names[] (per-segment exactness:
#    an unreferenced entry elsewhere in canon.json must never leak in).
# ---------------------------------------------------------------------------


def test_used_terms_hash_exact_scope(tmp_path):
    root = make_durable_root(tmp_path)
    canon = default_canon()  # entries: Jean (referenced), Marie (decoy, unreferenced)
    write_canon(root, canon)

    seg = default_segpack()
    seg["canon_names"] = ["Jean", "Ghost"]  # "Ghost" not in canon.entries at all
    seg["new_names"] = ["Pierre"]  # not in canon.entries either
    write_segpack(root, "seg01", seg)

    expected = hashlib.sha1(
        canonical_json_bytes({"Jean": canon["entries"]["Jean"]})
    ).hexdigest()
    assert one_field(root, "used_terms_hash", seg="seg01") == expected

    # Changing an entry this segment never references ("Marie") must not
    # move the segment's used_terms_hash at all.
    canon_marie_changed = json.loads(json.dumps(canon))
    canon_marie_changed["entries"]["Marie"]["target"] = "СОВСЕМ ДРУГОЕ"
    write_canon(root, canon_marie_changed)
    assert one_field(root, "used_terms_hash", seg="seg01") == expected


# ---------------------------------------------------------------------------
# 3a/3b. The two asymmetric used_terms_hash cases (new_names[] inclusion).
# ---------------------------------------------------------------------------


def test_used_terms_hash_uncanonized_new_name_contributes_nothing(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, {"entries": {"Jean": {"target": "Жан"}}, "review_queue": []})

    seg_with_uncanonized_pierre = default_segpack()
    seg_with_uncanonized_pierre["canon_names"] = ["Jean"]
    seg_with_uncanonized_pierre["new_names"] = ["Pierre"]  # NOT in canon.entries
    write_segpack(root, "seg01", seg_with_uncanonized_pierre)
    hash_with_uncanonized_pierre = one_field(root, "used_terms_hash", seg="seg01")

    seg_without_pierre_at_all = json.loads(json.dumps(seg_with_uncanonized_pierre))
    seg_without_pierre_at_all["new_names"] = []
    write_segpack(root, "seg01", seg_without_pierre_at_all)
    hash_without_pierre_at_all = one_field(root, "used_terms_hash", seg="seg01")

    # An uncanonized new_names[] entry is byte-for-byte indistinguishable
    # from that name not being listed at all -- it contributes nothing.
    assert hash_with_uncanonized_pierre == hash_without_pierre_at_all


def test_used_terms_hash_flips_stale_once_canonized_elsewhere(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, {"entries": {"Jean": {"target": "Жан"}}, "review_queue": []})

    seg = default_segpack()
    seg["canon_names"] = ["Jean"]
    seg["new_names"] = ["Pierre"]
    write_segpack(root, "seg01", seg)
    hash_before_canonization = one_field(root, "used_terms_hash", seg="seg01")

    # "Canonized elsewhere": some OTHER segment's glossary pass adds Pierre
    # to canon.json. THIS segment's own segpack file is never touched.
    write_canon(
        root,
        {
            "entries": {
                "Jean": {"target": "Жан"},
                "Pierre": {"target": "Пьер"},
            },
            "review_queue": [],
        },
    )
    hash_after_canonization = one_field(root, "used_terms_hash", seg="seg01")

    # The moment Pierre is canonized, his bytes enter this segment's hash for
    # the first time -- correctly flipping it stale, with no persisted
    # reverse index required (purely a live re-check).
    assert hash_after_canonization != hash_before_canonization


# ---------------------------------------------------------------------------
# 4. pipeline_version -- read verbatim (copied through, NEVER hashed).
# ---------------------------------------------------------------------------


def test_pipeline_version_verbatim_copy(tmp_path):
    root = make_durable_root(tmp_path)

    set_profile_value(root, "project.pipeline_version", "v9.9.9-rc1+build.42")
    assert one_field(root, "pipeline_version") == "v9.9.9-rc1+build.42"

    set_profile_value(root, "project.pipeline_version", "v10.0.0")
    assert one_field(root, "pipeline_version") == "v10.0.0"


# ---------------------------------------------------------------------------
# 5. schema_hash -- sha1 of concatenated, FILENAME-sorted bytes of the
#    project-local draft/review/segpack schema files.
# ---------------------------------------------------------------------------


def test_schema_hash_exact_scope(tmp_path):
    root = make_durable_root(tmp_path)
    draft_b = b'{"draft":"A"}'
    review_b = b'{"review":"B"}'
    segpack_b = b'{"segpack":"C"}'
    (root / "schemas" / "draft.schema.json").write_bytes(draft_b)
    (root / "schemas" / "review.schema.json").write_bytes(review_b)
    (root / "schemas" / "segpack.schema.json").write_bytes(segpack_b)

    # Filename-sorted: draft.schema.json < review.schema.json < segpack.schema.json.
    expected = hashlib.sha1(draft_b + review_b + segpack_b).hexdigest()
    assert one_field(root, "schema_hash") == expected

    (root / "schemas" / "segpack.schema.json").write_bytes(b'{"segpack":"CHANGED"}')
    changed = one_field(root, "schema_hash")
    assert changed != expected
    assert changed == hashlib.sha1(draft_b + review_b + b'{"segpack":"CHANGED"}').hexdigest()


# ---------------------------------------------------------------------------
# 6. prompt_hash -- sha1 of concatenated, filename-sorted bytes of the
#    INSTANTIATED translate_TASK.md/review_TASK.md (never the .template
#    infixed source files).
# ---------------------------------------------------------------------------


def test_prompt_hash_exact_scope(tmp_path):
    root = make_durable_root(tmp_path)
    translate_b = b"TRANSLATE PROMPT vX\n"
    review_b = b"REVIEW PROMPT vX\n"
    (root / "translate_TASK.md").write_bytes(translate_b)
    (root / "review_TASK.md").write_bytes(review_b)

    # Filename-sorted: 'review_TASK.md' < 'translate_TASK.md' ('r' < 't').
    expected = hashlib.sha1(review_b + translate_b).hexdigest()
    assert one_field(root, "prompt_hash") == expected

    # The .template-suffixed source files are never read at all.
    (root / "translate_TASK.md.template").write_bytes(b"DECOY -- must never be hashed\n")
    (root / "review_TASK.md.template").write_bytes(b"DECOY -- must never be hashed\n")
    assert one_field(root, "prompt_hash") == expected


# ---------------------------------------------------------------------------
# 7. agent_config_hash -- sha1 of canonical {effort, max_fix_rounds} ONLY.
#    (The dedicated batch_agent_cap NEGATIVE case is further below.)
# ---------------------------------------------------------------------------


def test_agent_config_hash_exact_scope(tmp_path):
    root = make_durable_root(tmp_path)
    set_profile_value(root, "engine.effort", "high")
    set_profile_value(root, "engine.max_fix_rounds", 5)
    set_profile_value(root, "engine.batch_agent_cap", 42)

    expected = hashlib.sha1(
        canonical_json_bytes({"effort": "high", "max_fix_rounds": 5})
    ).hexdigest()
    assert one_field(root, "agent_config_hash") == expected

    set_profile_value(root, "engine.effort", "low")
    changed = one_field(root, "agent_config_hash")
    assert changed != expected
    assert changed == hashlib.sha1(
        canonical_json_bytes({"effort": "low", "max_fix_rounds": 5})
    ).hexdigest()


# ---------------------------------------------------------------------------
# 8. profile_semantics_hash -- sha1 of exactly SIX named profile fields, no
#    more, no fewer.
# ---------------------------------------------------------------------------


def test_profile_semantics_hash_exact_scope(tmp_path):
    root = make_durable_root(tmp_path)
    obj = {
        "source_lang": "fr",
        "target_lang": "ru",
        "verse_policy_mode": "full_rhymed_plus_literal",
        "verse_policy_threshold_lines": 4,
        "apparatus_policy": "translate_all",
        "untranslated_sentinel": "[TODO-UNTRANSLATED]",
    }
    expected = hashlib.sha1(canonical_json_bytes(obj)).hexdigest()
    assert one_field(root, "profile_semantics_hash") == expected

    # An unrelated profile field (not one of the six -- deliberately NOT
    # duplicating agent_config_hash's own fields either) must not move it.
    set_profile_value(root, "project.pipeline_version", "vSOMETHING-ELSE")
    set_profile_value(root, "engine.effort", "high")
    assert one_field(root, "profile_semantics_hash") == expected


# ---------------------------------------------------------------------------
# 9. particle_config_hash -- sha1 of the RESOLVED particle_config file's raw
#    bytes, never reconstructed from language.code, never keyed by filename.
# ---------------------------------------------------------------------------


def test_particle_config_hash_exact_scope(tmp_path):
    root = make_durable_root(tmp_path)
    content = b'{"particles": ["de", "du", "des"]}'
    (root / "languages" / "fr_particles.json").write_bytes(content)
    expected = hashlib.sha1(content).hexdigest()
    assert one_field(root, "particle_config_hash") == expected

    # Only the file's raw BYTES matter, never the filename: pointing the
    # profile at a DIFFERENTLY-NAMED file with the SAME bytes must yield the
    # identical hash.
    (root / "languages" / "renamed_particles.json").write_bytes(content)
    set_profile_value(root, "source.language.particle_config", "renamed_particles.json")
    assert one_field(root, "particle_config_hash") == expected

    # Different bytes -> different hash.
    (root / "languages" / "renamed_particles.json").write_bytes(content + b"\nEXTRA")
    assert one_field(root, "particle_config_hash") != expected


# ---------------------------------------------------------------------------
# 10. source_extraction_hash -- sha1 of canonical {format, adapter_config}
#     (ONLY the sub-block matching the resolved format) CONCATENATED with the
#     resolved extractor file's raw bytes.
# ---------------------------------------------------------------------------


def test_source_extraction_hash_exact_scope(tmp_path):
    root = make_durable_root(tmp_path)
    set_profile_value(root, "source.format", "plain_text")

    def set_adapter_config(profile):
        profile["source"]["adapter_config"]["plain_text"] = {"encoding": "utf-8"}
        profile["source"]["adapter_config"]["gutenberg_epub"] = {"strip_toc": True}

    edit_profile(root, set_adapter_config)

    extract_bytes = b"# extract.py vFIX\n"
    (root / "extract.py").write_bytes(extract_bytes)

    expected_obj = {"format": "plain_text", "adapter_config": {"encoding": "utf-8"}}
    expected = hashlib.sha1(canonical_json_bytes(expected_obj) + extract_bytes).hexdigest()
    assert one_field(root, "source_extraction_hash") == expected

    # Mutating the UNUSED gutenberg_epub sub-block must not move the hash --
    # only the ONE sub-block matching source.format is ever hashed, never
    # the whole adapter_config object.
    def mutate_decoy_subblock(profile):
        profile["source"]["adapter_config"]["gutenberg_epub"] = {
            "strip_toc": False,
            "extra_junk_field": "ignored",
        }

    edit_profile(root, mutate_decoy_subblock)
    assert one_field(root, "source_extraction_hash") == expected

    # Mutating the extractor file's own bytes DOES change it.
    (root / "extract.py").write_bytes(extract_bytes + b"# changed\n")
    assert one_field(root, "source_extraction_hash") != expected


# ---------------------------------------------------------------------------
# 11. source_input_hash -- sha1 of canonical {source_path, source_bytes_sha1};
#     source_path is the literal profile string (part of the hash), never
#     merely derived from the file's bytes.
# ---------------------------------------------------------------------------


def test_source_input_hash_exact_scope(tmp_path):
    root = make_durable_root(tmp_path)
    source_file = root / "source_original.txt"
    source_bytes = b"Le texte source, version fixe pour ce test.\n"
    source_file.write_bytes(source_bytes)
    write_manifest(root, [str(source_file.resolve())])
    set_profile_value(root, "source.path", "/logical/original/path.txt")
    set_profile_value(root, "source.format", "plain_text")

    source_bytes_sha1 = hashlib.sha1(source_bytes).hexdigest()
    expected = hashlib.sha1(
        canonical_json_bytes(
            {"source_path": "/logical/original/path.txt", "source_bytes_sha1": source_bytes_sha1}
        )
    ).hexdigest()
    assert one_field(root, "source_input_hash") == expected

    # Changing ONLY the logical source_path string (file bytes untouched)
    # must change the hash -- source_path is hashed directly, not merely
    # implied by the file content.
    set_profile_value(root, "source.path", "/a/completely/different/logical/path.txt")
    assert one_field(root, "source_input_hash") != expected


# ---------------------------------------------------------------------------
# 12. derivation_bundle_hash -- sha1 of sorted-concatenated raw bytes of
#     EXACTLY bootstrap_names.py + segpack.py (never any other script).
# ---------------------------------------------------------------------------


def test_derivation_bundle_hash_exact_scope(tmp_path):
    root = make_durable_root(tmp_path)
    bootstrap_b = b"# bootstrap_names.py fixture vX\n"
    segpack_b = b"# segpack.py fixture vX\n"
    (root / "scripts" / "bootstrap_names.py").write_bytes(bootstrap_b)
    (root / "scripts" / "segpack.py").write_bytes(segpack_b)

    # Filename-sorted: 'bootstrap_names.py' < 'segpack.py'.
    expected = hashlib.sha1(bootstrap_b + segpack_b).hexdigest()
    assert one_field(root, "derivation_bundle_hash") == expected

    # An unrelated script under scripts/ (not one of the exactly-two members)
    # must never move this hash.
    (root / "scripts" / "validate_draft.py").write_bytes(b"# unrelated decoy script, changed\n")
    assert one_field(root, "derivation_bundle_hash") == expected


# ---------------------------------------------------------------------------
# 13. verse_map_hash -- sha1 of THIS segment's verses[] projected to
#     EXACTLY {vid, placeholder, parent_block}; extra fields ignored.
# ---------------------------------------------------------------------------


def test_verse_map_hash_exact_scope(tmp_path):
    root = make_durable_root(tmp_path)
    seg = default_segpack()
    seg["verses"] = [
        {
            "vid": "v1",
            "placeholder": "⟦VERSE_v1⟧",
            "parent_block": "b0",
            "raw_text": "IGNORE ME -- not part of the projection",
        }
    ]
    write_segpack(root, "seg01", seg)

    expected = hashlib.sha1(
        canonical_json_bytes(
            [{"vid": "v1", "placeholder": "⟦VERSE_v1⟧", "parent_block": "b0"}]
        )
    ).hexdigest()
    assert one_field(root, "verse_map_hash", seg="seg01") == expected

    # An extra field NOT in {vid, placeholder, parent_block} is ignored.
    seg_extra_changed = json.loads(json.dumps(seg))
    seg_extra_changed["verses"][0]["raw_text"] = "SOMETHING TOTALLY DIFFERENT"
    write_segpack(root, "seg01", seg_extra_changed)
    assert one_field(root, "verse_map_hash", seg="seg01") == expected

    # A field that DOES belong to the projection changes the hash.
    seg_placeholder_changed = json.loads(json.dumps(seg))
    seg_placeholder_changed["verses"][0]["placeholder"] = "⟦VERSE_DIFFERENT⟧"
    write_segpack(root, "seg01", seg_placeholder_changed)
    assert one_field(root, "verse_map_hash", seg="seg01") != expected


# ---------------------------------------------------------------------------
# 14. note_map_hash -- sha1 of THIS segment's footnotes[] projected to
#     EXACTLY {n, source_text}; extra fields ignored.
# ---------------------------------------------------------------------------


def test_note_map_hash_exact_scope(tmp_path):
    root = make_durable_root(tmp_path)
    seg = default_segpack()
    seg["footnotes"] = [
        {"n": 1, "source_text": "a footnote", "translator_note": "IGNORE ME"}
    ]
    write_segpack(root, "seg01", seg)

    expected = hashlib.sha1(
        canonical_json_bytes([{"n": 1, "source_text": "a footnote"}])
    ).hexdigest()
    assert one_field(root, "note_map_hash", seg="seg01") == expected

    # An extra field NOT in {n, source_text} is ignored.
    seg_extra_changed = json.loads(json.dumps(seg))
    seg_extra_changed["footnotes"][0]["translator_note"] = "SOMETHING ELSE ENTIRELY"
    write_segpack(root, "seg01", seg_extra_changed)
    assert one_field(root, "note_map_hash", seg="seg01") == expected

    # source_text itself DOES change the hash.
    seg_text_changed = json.loads(json.dumps(seg))
    seg_text_changed["footnotes"][0]["source_text"] = "a DIFFERENT footnote"
    write_segpack(root, "seg01", seg_text_changed)
    assert one_field(root, "note_map_hash", seg="seg01") != expected


# ---------------------------------------------------------------------------
# 15. plugin_bundle_hash -- read VERBATIM from the runs/.plugin_bundle_hash
#     marker file (stripped), NEVER recomputed per segment even though the
#     bundle's member scripts live right there under scripts/.
# ---------------------------------------------------------------------------


def test_plugin_bundle_hash_read_from_marker_verbatim(tmp_path):
    root = make_durable_root(tmp_path)
    (root / "runs" / ".plugin_bundle_hash").write_text(
        "not-a-real-sha1-marker-value-XYZ\n", encoding="utf-8"
    )
    assert one_field(root, "plugin_bundle_hash") == "not-a-real-sha1-marker-value-XYZ"

    # Changing the bytes of scripts that NOMINALLY make up this bundle must
    # NOT change the reported value -- it's read from the marker, not
    # recomputed live.
    (root / "scripts" / "validate_draft.py").write_bytes(b"# totally different bytes now\n")
    cache_key_copy = root / "scripts" / "cache_key.py"
    cache_key_copy.write_bytes(cache_key_copy.read_bytes() + b"\n# trailing harmless comment\n")

    assert one_field(root, "plugin_bundle_hash") == "not-a-real-sha1-marker-value-XYZ"


# ---------------------------------------------------------------------------
# 16. NEGATIVE CASE -- engine.batch_agent_cap is DELIBERATELY EXCLUDED from
#     agent_config_hash and therefore from the full 15-field cache_key.
#     Changing it alone must never flip any segment stale.
# ---------------------------------------------------------------------------


def test_batch_agent_cap_excluded_and_never_invalidates_any_segment(tmp_path):
    root = make_durable_root(tmp_path)
    set_profile_value(root, "engine.batch_agent_cap", 1000)
    set_profile_value(root, "engine.effort", "medium")
    set_profile_value(root, "engine.max_fix_rounds", 3)
    baseline = full_key(root, seg="seg01")

    # Change ONLY batch_agent_cap -- a pure orchestration/scheduling knob --
    # leaving effort/max_fix_rounds and every other field's inputs untouched.
    set_profile_value(root, "engine.batch_agent_cap", 5)
    after = full_key(root, seg="seg01")

    assert_only_changed(baseline, after, expected=set())
    assert after == baseline, (
        "changing engine.batch_agent_cap alone must not change ANY of the "
        "15 cache_key fields -- it would invalidate every converged segment "
        f"on a mere batch-size tweak: before={baseline} after={after}"
    )

    # And directly confirm agent_config_hash's own byte-scope excludes it:
    # the field equals the hash of {effort, max_fix_rounds} alone.
    expected_agent_config_hash = hashlib.sha1(
        canonical_json_bytes({"effort": "medium", "max_fix_rounds": 3})
    ).hexdigest()
    assert baseline["agent_config_hash"] == expected_agent_config_hash
    assert after["agent_config_hash"] == expected_agent_config_hash
