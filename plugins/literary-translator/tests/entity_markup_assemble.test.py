"""tests/entity_markup_assemble.test.py -- #795, the ASSEMBLE half of
`output.entity_markup`: strip the translator's own inline entity elements out
of the assembled book and, in `index` mode, record what was marked.

## Fixture strategy

Identical in kind to tests/assemble.test.py, and restated here rather than
imported -- house convention is one self-contained file per test module. Every
test builds a REAL durable_root on disk (manifest, segpack, draft, materialized
ledger with a REAL cache key computed by the shipped cache_key.py, canon.json,
profile.yml + ownership marker), copies the ACTUAL shipped scripts into
`{root}/scripts/` so `Path(__file__).resolve().parents[1]` self-anchors against
the fixture exactly as it does in production, and runs `assemble.py` as a
subprocess with no CLI flags.

## What this file asserts, and what it deliberately does not

The three surfaces this half owns: the process contract (exit code + EXACTLY
one JSON line on stdout), the persisted `nodestream.json`, and the summary
JSON's own `entity_markup` counts. It never asserts against the rendered vault
-- delivery to a reader is `render_obsidian.py`'s half of #795 and is proved in
tests/entity_markup_render.test.py against the WRITTEN vault files.

Failure paths carry their operator detail in the one JSON line's `error` field
rather than on stderr: that is assemble.py's existing convention for every
`AssembleError` it already raises (`main()` prints one JSON line and nothing
else), and #795 does not change it. stderr is still captured and folded into
every assertion message, so a crash or an unexpected warning is visible when a
case fails.

## The one book these tests vary

A single segment `seg01` carrying, in reading order, one of each carrier the
markup pass must see:

  h1       HEAD  -> kind "heading"
  p1       PARA  -> kind "prose", anchors footnote 1
  p2       PARA  -> kind "prose", carries an INLINE-mount verse (vB)
  vblockA  VERSE -> kind "verse" (block-mount vA), whose own `text` the
                    obsidian adapter never emits -- the `span_unrendered` case
  FN1            -> footnote 1's definition block (never in block_ids)

Every text-bearing string is a keyword argument of `build_book()`, so a test
changes exactly the carrier it is about and inherits a clean book everywhere
else.
"""
import ast
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

ASSEMBLE_SRC = SCRIPTS_SRC_DIR / "assemble.py"
OUTPUT_RESOLVE_SRC = SCRIPTS_SRC_DIR / "output_resolve.py"
RENDER_OBSIDIAN_SRC = SCRIPTS_SRC_DIR / "render_obsidian.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
CACHE_KEY_SRC = SCRIPTS_SRC_DIR / "cache_key.py"
JSON_STDOUT_SRC = SCRIPTS_SRC_DIR / "json_stdout.py"

for _src in (
    ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC, VALIDATE_DRAFT_SRC,
    CACHE_KEY_SRC, JSON_STDOUT_SRC,
):
    assert _src.is_file(), f"shipped script not found at {_src}"

FN_PH_1 = "⟦FNREF_1⟧"
V_PH_A = "⟦VERSE_vA_abc12345⟧"
V_PH_B = "⟦VERSE_vB_def67890⟧"


# ---------------------------------------------------------------------------
# Fixture builders (restated from tests/assemble.test.py -- see this module's
# docstring for why they are not imported).
# ---------------------------------------------------------------------------


def _yaml_dump(obj) -> str:
    import yaml

    return yaml.safe_dump(obj, sort_keys=False)


def _write_cache_key_inputs(root: Path, scripts_dir: Path) -> None:
    """The durable-root files cache_key.py's own field computers read. Content
    is irrelevant everywhere except style_bible.md, whose two STYLE_CONTRACT
    markers compute_style_contract_hash() requires exactly once each."""
    for name, body in (
        ("bootstrap_names.py", b"# bootstrap_names.py fixture\n"),
        ("segpack.py", b"# segpack.py fixture\n"),
    ):
        if not (scripts_dir / name).exists():
            (scripts_dir / name).write_bytes(body)
    (root / "style_bible.md").write_bytes(
        b"# Style Bible\n\n<!-- STYLE_CONTRACT_BEGIN -->\n"
        b"Formal register, Oxford comma.\n<!-- STYLE_CONTRACT_END -->\n"
    )
    (root / "translate_TASK.md").write_bytes(b"TRANSLATE TASK PROMPT v1\n")
    (root / "review_TASK.md").write_bytes(b"REVIEW TASK PROMPT v1\n")
    (root / "extract.py").write_bytes(b"# extract.py fixture v1\n")
    (root / "source.txt").write_bytes(b"Ceci est un texte source de test.\n")
    languages_dir = root / "languages"
    languages_dir.mkdir(exist_ok=True)
    (languages_dir / "fr_test.json").write_text(
        json.dumps(
            {
                "PARTICLES": ["de", "du", "des"],
                "STOPWORDS": ["le", "la", "les"],
                "has_elision": False,
                "ELISION_RE": None,
            }
        ),
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


def default_profile(output_target="obsidian", entity_markup=None, custom_renderer_path=None):
    """`entity_markup`: the literal value to write at `output.entity_markup`,
    or the module-level `_OMIT` sentinel to leave the key out entirely. Absence
    and a present-but-malformed block are DIFFERENT states (`off` vs a runtime
    refusal), so the fixture has to be able to express both."""
    output_cfg = {
        "v1_scope": "assembled_book",
        "destination": "/placeholder/out/",
        "target": output_target,
        "name_display": {"parenthetical_originals": "never"},
        "adapter_config": {
            "obsidian": {"folders": {}, "mentions_section": {"enabled": False}},
            "epub": None,
            "custom": {"renderer_path": custom_renderer_path} if custom_renderer_path else None,
        },
    }
    if entity_markup is not _OMIT:
        output_cfg["entity_markup"] = entity_markup
    return {
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
                "particle_config": "fr_test.json",
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
        "output": output_cfg,
    }


class _Omit:
    def __repr__(self):
        return "<omit>"


_OMIT = _Omit()


def make_root(tmp_path, output_target="obsidian", entity_markup=_OMIT,
              custom_renderer_path=None) -> Path:
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (
        ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC, VALIDATE_DRAFT_SRC,
        CACHE_KEY_SRC, JSON_STDOUT_SRC,
    ):
        shutil.copy2(src, scripts_dir / src.name)

    profile = default_profile(
        output_target=output_target, entity_markup=entity_markup,
        custom_renderer_path=custom_renderer_path,
    )
    profile["project"]["durable_root"] = str(root)
    profile["output"]["destination"] = str(root / "out")
    (root / "profile.yml").write_text(_yaml_dump(profile), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )
    (root / "canon.json").write_text(
        json.dumps(
            {
                "entries": {},
                "review_queue": [],
                "generation_hashes": {
                    "particle_config_hash": "x",
                    "derivation_bundle_hash": "y",
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "segments").mkdir()
    (root / "runs").mkdir()
    _write_cache_key_inputs(root, scripts_dir)
    return root


def draft_content_sha1_of(doc: dict) -> str:
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def real_cache_key(root: Path, seg: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "cache_key.py"), "--seg", seg],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"fixture setup: cache_key.py --seg {seg} failed:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


def build_book(
    root: Path,
    *,
    heading_text="Chapitre un",
    p1_text=f"Prose one {FN_PH_1} done.",
    p2_text=f"Inline {V_PH_B} carrier.",
    vblock_text=V_PH_A,
    footnote_text="Footnote definition text.",
    va_rendered="Line one\nLine two",
    va_gloss="Gloss for verse A",
    vb_rendered="Inline verse line",
    vb_gloss="Gloss for verse B",
) -> None:
    """The one book every test in this file varies -- see the module docstring
    for its five blocks and which carrier each one stands for."""
    blocks = {
        "h1": {"type": "HEAD", "seg": "seg01", "order_index": 0,
               "plain_text": "Chapitre un"},
        "p1": {"type": "PARA", "seg": "seg01", "order_index": 1,
               "plain_text": "Prose une.", "fnrefs": [1]},
        "p2": {"type": "PARA", "seg": "seg01", "order_index": 2,
               "plain_text": f"Inline {V_PH_B} porteur."},
        "vblockA": {"type": "VERSE", "seg": "seg01", "order_index": 3,
                    "plain_text": V_PH_A},
        "FN1": {"type": "FN", "seg": None, "order_index": 4,
                "plain_text": "Texte de la note."},
    }
    for bid, block in blocks.items():
        block.setdefault("id", bid)
        block.setdefault("sha1", hashlib.sha1(bid.encode()).hexdigest())
        block.setdefault("source_file", "source.txt")
    manifest = {
        "blocks": blocks,
        "spine": [{"pos": 0, "file": "source.txt", "klass": "body"}],
        "segments": [
            {"seg": "seg01", "kind": "body", "title_text": "Chapitre un",
             "block_ids": ["h1", "p1", "p2", "vblockA"], "word_count": 100},
        ],
        "footnotes": [
            {"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"}
        ],
        "frontback": [],
        "verse": {
            "store": [
                # `plain_text` deliberately carries NO ⟦FNREF_n⟧: the per-verse
                # anchor-coverage gate (#433) derives its expectation from this
                # field, and a fixture that stated one there would refuse every
                # test whose translated verse legitimately does not cite it.
                {"vid": "vA", "placeholder": V_PH_A, "context": "body",
                 "parent_block": "vblockA", "mount": "block",
                 "plain_text": "Ligne une\nLigne deux"},
                {"vid": "vB", "placeholder": V_PH_B, "context": "body",
                 "parent_block": "p2", "mount": "inline",
                 "plain_text": "Ligne en ligne"},
            ]
        },
        "source_inputs": ["source.txt"],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    segpack = {
        "seg": "seg01",
        "title": "seg01",
        "kind": "body",
        "word_count": 10,
        "blocks": [
            {"id": "h1", "order_index": 0, "plain_text": "Chapitre un"},
            {"id": "p1", "order_index": 1, "plain_text": "Prose une."},
            {"id": "p2", "order_index": 2, "plain_text": f"Inline {V_PH_B} porteur."},
            {"id": "vblockA", "order_index": 3, "plain_text": V_PH_A},
        ],
        "footnotes": [{"n": 1, "source_text": "Texte de la note."}],
        "verses": [
            {"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"},
            {"vid": "vB", "placeholder": V_PH_B, "parent_block": "p2"},
        ],
        "names": [],
        "canon_names": [],
        "new_names": [],
        "generation_hashes": {
            "source_extraction_hash": "x",
            "source_input_hash": "y",
            "particle_config_hash": "x",
            "derivation_bundle_hash": "y",
        },
    }
    (root / "segments" / "segpack_seg01.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8"
    )

    draft = {
        "seg": "seg01",
        "blocks": {
            "h1": heading_text,
            "p1": p1_text,
            "p2": p2_text,
            "vblockA": vblock_text,
        },
        "footnotes": {"1": footnote_text},
        "verses": {
            "vA": {"rendered": va_rendered, "literal_gloss": va_gloss},
            "vB": {"rendered": vb_rendered, "literal_gloss": vb_gloss},
        },
        "names": [],
        "notes": [],
    }
    (root / "segments" / "seg01.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8"
    )

    (root / "runs" / "ledger.json").write_text(
        json.dumps(
            {
                "segments": {
                    "seg01": {
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "status": "converged",
                        "rounds": 1,
                        "cache_key": real_cache_key(root, "seg01"),
                        "n_blocks": 4,
                        "n_footnotes": 1,
                        "n_verses": 2,
                        "reviewed_draft_sha1": draft_content_sha1_of(draft),
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def run_assemble(root: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "assemble.py")],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_one_json_line(proc: subprocess.CompletedProcess) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one stdout JSON line, got {len(lines)}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(lines[0])


def read_nodestream(root: Path) -> dict:
    path = root / "out" / ".assembled" / "nodestream.json"
    assert path.is_file(), f"expected nodestream.json artifact at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def node_by_id(nodestream: dict, node_id: str) -> dict:
    for node in nodestream["nodes"]:
        if node["id"] == node_id:
            return node
    raise AssertionError(f"no node {node_id!r} in {[n['id'] for n in nodestream['nodes']]}")


def footnote_text(nodestream: dict, n: int) -> str:
    for fn in nodestream["footnotes"]:
        if fn["n"] == n:
            return fn["text"]
    raise AssertionError(f"no footnote n={n} in {nodestream['footnotes']}")


def assert_ok(proc: subprocess.CompletedProcess) -> dict:
    assert proc.returncode == 0, (
        f"expected exit 0, got {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = parse_one_json_line(proc)
    assert payload["success"] is True, payload
    return payload


def assert_refused(proc: subprocess.CompletedProcess, reason: str) -> dict:
    assert proc.returncode == 1, (
        f"expected exit 1 ({reason}), got {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = parse_one_json_line(proc)
    assert payload["success"] is False, payload
    assert payload.get("reason") == reason, (
        f"expected reason {reason!r}, got {payload.get('reason')!r} -- "
        f"{payload.get('error')!r}\nstderr:\n{proc.stderr}"
    )
    return payload


PERSON_PLACE = {"tags": ["person", "place"]}
PERSON_PLACE_INDEX = {"tags": ["person", "place"], "index_from": "markup"}


# ===========================================================================
# 1. Block absent -> mode `off`: no scan at all, no key anywhere.
# ===========================================================================


def test_absent_block_scans_nothing_and_adds_no_key(tmp_path):
    """The knob's whole back-compat promise. The declared-looking markup is
    left in the draft on purpose: with no `output.entity_markup` block the text
    must survive BYTE-FOR-BYTE, which is what distinguishes "did not scan" from
    "scanned and found nothing"."""
    root = make_root(tmp_path)
    marked = f"Prose <person ref=\"jean\">Jean</person> one {FN_PH_1} done."
    build_book(root, p1_text=marked)

    proc = run_assemble(root)
    payload = assert_ok(proc)

    assert "entity_markup" not in payload, payload
    ns = read_nodestream(root)
    assert "entity_markup" not in ns, sorted(ns)
    assert node_by_id(ns, "p1")["text"] == marked


# ===========================================================================
# 2. Runtime config validation, on assemble.py's OWN path.
#
# profile.schema.json never runs here: assemble.py loads profile.yml through
# validate_draft.load_profile(), which parses YAML and returns a mapping
# without jsonschema, and assemble.py is directly invocable on a resumed
# project whose profile may have been hand-edited since Step 0.
# ===========================================================================


@pytest.mark.parametrize(
    "block, expect_in_error",
    [
        pytest.param({"tags": []}, "empty", id="tags-empty-list"),
        pytest.param({"ref_attribute": "ref"}, "tags", id="tags-missing"),
        pytest.param({"tags": ["person", "person"]}, "person", id="tags-duplicate"),
        pytest.param({"tags": ["person", 7]}, "non-string", id="tags-non-string-member"),
        pytest.param({"tags": ["Person"]}, "Person", id="tags-pattern-uppercase"),
        pytest.param({"tags": ["9lives"]}, "9lives", id="tags-pattern-leading-digit"),
        pytest.param([], "mapping", id="block-not-a-mapping-list"),
        pytest.param("person", "mapping", id="block-not-a-mapping-string"),
        pytest.param(None, "mapping", id="block-not-a-mapping-null"),
        pytest.param(
            {"tags": ["person"], "index_from": "markkup"}, "index_from",
            id="index-from-typo",
        ),
        pytest.param(
            {"tags": ["person"], "tag": ["person"]}, "tag", id="unknown-key",
        ),
        pytest.param(
            {"tags": ["person"], "ref_attribute": 7}, "ref_attribute",
            id="ref-attribute-non-string",
        ),
        pytest.param(
            {"tags": ["person"], "ref_attribute": "Ref"}, "ref_attribute",
            id="ref-attribute-pattern",
        ),
        pytest.param(
            {"tags": ["person"], "ref_attribute": None}, "ref_attribute",
            id="ref-attribute-null-is-not-the-documented-default",
        ),
    ],
)
def test_malformed_config_block_is_refused_at_runtime(tmp_path, block, expect_in_error):
    root = make_root(tmp_path, entity_markup=block)
    build_book(root)

    payload = assert_refused(run_assemble(root), "entity_markup_config_invalid")
    assert expect_in_error in payload["error"], payload["error"]


def test_bare_string_tags_is_refused_rather_than_read_character_by_character(tmp_path):
    """THE load-bearing config case. `tags: person` is a perfectly good YAML
    string and a string is ITERABLE, so an unvalidated reader builds a
    per-CHARACTER alternation (`p|e|r|s|o|n`) and reports a successful run --
    a green that silently mangles the book. The fixture text carries no markup
    at all, so an implementation that skipped this check would exit 0 here."""
    root = make_root(tmp_path, entity_markup={"tags": "person"})
    build_book(root)

    payload = assert_refused(run_assemble(root), "entity_markup_config_invalid")
    assert "tags" in payload["error"]
    assert "list" in payload["error"], payload["error"]


# ===========================================================================
# 3. `ref_attribute` is the operator's choice, and nothing else is special.
# ===========================================================================


def test_non_default_ref_attribute_is_honoured(tmp_path):
    root = make_root(
        tmp_path,
        entity_markup={"tags": ["person"], "ref_attribute": "who", "index_from": "markup"},
    )
    build_book(root, p1_text=f"Prose <person who=\"jean-x\">Jean</person> {FN_PH_1} done.")

    assert_ok(run_assemble(root))
    spans = read_nodestream(root)["entity_markup"]["spans"]
    assert list(spans) == ["1"]
    assert spans["1"] == {"tag": "person", "payload": "Jean", "ref": "jean-x"}


def test_plain_ref_attribute_is_not_special_under_a_declared_alternative(tmp_path):
    """With `ref_attribute: who`, `ref="..."` is ordinary attribute text -- and
    the lexical guard therefore refuses it rather than silently accepting an
    element whose declared shape it does not match."""
    root = make_root(
        tmp_path,
        entity_markup={"tags": ["person"], "ref_attribute": "who", "index_from": "markup"},
    )
    build_book(root, p1_text=f"Prose <person ref=\"jean\">Jean</person> {FN_PH_1} done.")

    assert_refused(run_assemble(root), "entity_markup_malformed")


# ===========================================================================
# 4. `strip` mode: elements gone from every carrier, payload intact, counted.
# ===========================================================================


def test_strip_mode_removes_elements_from_every_carrier(tmp_path):
    root = make_root(tmp_path, entity_markup=PERSON_PLACE)
    build_book(
        root,
        heading_text="Chapitre de <place>Kiev</place>",
        p1_text=f"Prose <person ref=\"jean\">Jean</person> {FN_PH_1} done.",
        p2_text=f"Inline <person>Anne</person> {V_PH_B} carrier.",
        footnote_text="Note about <person>Marc</person> here.",
        va_rendered="Line about <place>Rome</place>",
        va_gloss="Gloss about <person>Paul</person>",
    )

    payload = assert_ok(run_assemble(root))
    ns = read_nodestream(root)

    assert node_by_id(ns, "h1")["text"] == "Chapitre de Kiev"
    assert node_by_id(ns, "p1")["text"] == f"Prose Jean {FN_PH_1} done."
    assert node_by_id(ns, "p2")["text"] == f"Inline Anne {V_PH_B} carrier."
    assert footnote_text(ns, 1) == "Note about Marc here."
    verse_a = node_by_id(ns, "vblockA")["verses"][0]["content"]
    assert verse_a["rendered"] == "Line about Rome"
    assert verse_a["literal_gloss"] == "Gloss about Paul"

    # STRIP records nothing: no key on the nodestream, and no ENT_ sentinel
    # anywhere in it (the FNREF/verse sentinels legitimately stay).
    assert "entity_markup" not in ns, sorted(ns)
    assert "ENT_" not in json.dumps(ns, ensure_ascii=False)

    assert payload["entity_markup"]["mode"] == "strip"
    assert payload["entity_markup"]["spans"] == 6
    assert payload["entity_markup"]["tags"] == {"person": 4, "place": 2}
    assert payload["entity_markup"]["strings_scanned"] >= 6


# ===========================================================================
# 5. `index` mode: paired sentinels in the prose, the spans recorded beside it.
# ===========================================================================


def test_index_mode_emits_paired_sentinels_and_records_every_span(tmp_path):
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(
        root,
        heading_text="Chapitre de <place>Kiev</place>",
        p1_text=f"Prose <person ref=\"jean\">Jean</person> {FN_PH_1} done.",
        p2_text=f"Inline <person>Anne</person> {V_PH_B} carrier.",
        va_rendered="Line about <place>Rome</place>",
    )

    payload = assert_ok(run_assemble(root))
    ns = read_nodestream(root)

    assert node_by_id(ns, "h1")["text"] == "Chapitre de ⟦ENT_1⟧Kiev⟦/ENT_1⟧"
    assert node_by_id(ns, "p1")["text"] == (
        f"Prose ⟦ENT_2⟧Jean⟦/ENT_2⟧ {FN_PH_1} done."
    )
    assert node_by_id(ns, "p2")["text"] == (
        f"Inline ⟦ENT_3⟧Anne⟦/ENT_3⟧ {V_PH_B} carrier."
    )
    assert node_by_id(ns, "vblockA")["verses"][0]["content"]["rendered"] == (
        "Line about ⟦ENT_4⟧Rome⟦/ENT_4⟧"
    )

    assert ns["entity_markup"] == {
        "spans": {
            "1": {"tag": "place", "payload": "Kiev"},
            "2": {"tag": "person", "payload": "Jean", "ref": "jean"},
            "3": {"tag": "person", "payload": "Anne"},
            "4": {"tag": "place", "payload": "Rome"},
        }
    }
    # `ref` is present ONLY where the attribute was: span 2 carries it, and the
    # other three have no `ref` key at all rather than a null one.
    assert "ref" not in ns["entity_markup"]["spans"]["1"]

    assert payload["entity_markup"]["mode"] == "index"
    assert payload["entity_markup"]["spans"] == 4
    assert payload["entity_markup"]["tags"] == {"person": 2, "place": 2}
    # The span count is the tag count -- one recorded span per marked element.
    assert len(ns["entity_markup"]["spans"]) == payload["entity_markup"]["spans"]


def test_index_mode_records_a_marked_name_in_a_heading_like_any_other(tmp_path):
    """A heading is not a special case at assemble time: the span is recorded
    exactly like a prose span, because resolving heading spans differently is
    what reopens the wrong-note problem inside a heading."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(root, heading_text="Le livre de <person ref=\"jean\">Jean</person>")

    assert_ok(run_assemble(root))
    ns = read_nodestream(root)
    assert node_by_id(ns, "h1")["text"] == "Le livre de ⟦ENT_1⟧Jean⟦/ENT_1⟧"
    assert ns["entity_markup"]["spans"]["1"] == {
        "tag": "person", "payload": "Jean", "ref": "jean",
    }


def test_index_mode_records_a_span_inside_a_footnote_definition(tmp_path):
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(root, footnote_text="Note about <person>Marc</person> here.")

    assert_ok(run_assemble(root))
    ns = read_nodestream(root)
    assert footnote_text(ns, 1) == "Note about ⟦ENT_1⟧Marc⟦/ENT_1⟧ here."
    assert ns["entity_markup"]["spans"]["1"] == {"tag": "person", "payload": "Marc"}


# ===========================================================================
# 6. Malformed pairing -- all four shapes, in both modes' shared parser.
# ===========================================================================


@pytest.mark.parametrize(
    "fragment",
    [
        pytest.param("<person>Jean", id="unclosed"),
        pytest.param("Jean</person>", id="stray-close"),
        pytest.param("<person>Jean <person>Anne</person></person>", id="nested"),
        pytest.param("<person>Jean</place>", id="mismatched-close"),
    ],
)
def test_malformed_pairing_is_refused(tmp_path, fragment):
    root = make_root(tmp_path, entity_markup=PERSON_PLACE)
    build_book(root, p1_text=f"Prose {fragment} {FN_PH_1} done.")

    assert_refused(run_assemble(root), "entity_markup_malformed")


# ===========================================================================
# 7. The lexical guard: a DECLARED name used in a shape the grammar does not
#    match is refused, never shipped verbatim.
# ===========================================================================


@pytest.mark.parametrize(
    "fragment",
    [
        pytest.param("<person/>", id="self-closing"),
        pytest.param("<person ref=x>Jean</person>", id="unquoted-attribute"),
        pytest.param("a bare <person", id="unterminated"),
        pytest.param("<person class=\"x\">Jean</person>", id="undeclared-attribute"),
    ],
)
def test_lexical_guard_refuses_a_malformed_use_of_a_declared_name(tmp_path, fragment):
    root = make_root(tmp_path, entity_markup=PERSON_PLACE)
    build_book(root, p1_text=f"Prose {fragment} {FN_PH_1} done.")

    assert_refused(run_assemble(root), "entity_markup_malformed")


def test_lexical_guard_covers_a_schema_valid_trailing_hyphen_tag(tmp_path):
    """The `person` cases above all end in an alphanumeric and would pass under
    a `\\b` terminator. The identifier pattern admits a tag ending in `-`, and
    Python places no word boundary between a trailing `-` and the `/` that
    follows it -- so this is the case that actually distinguishes the shipped
    negative lookahead from the `\\b` shortcut."""
    root = make_root(tmp_path, entity_markup={"tags": ["person-"]})
    build_book(root, p1_text=f"Prose <person-/> {FN_PH_1} done.")

    assert_refused(run_assemble(root), "entity_markup_malformed")


def test_a_trailing_hyphen_tag_still_works_when_well_formed(tmp_path):
    """The guard above must refuse the malformed use WITHOUT breaking the
    legitimate one -- otherwise the lookahead would be over-broad rather than
    correct."""
    root = make_root(
        tmp_path, entity_markup={"tags": ["person-"], "index_from": "markup"}
    )
    build_book(root, p1_text=f"Prose <person->Jean</person-> {FN_PH_1} done.")

    assert_ok(run_assemble(root))
    ns = read_nodestream(root)
    assert ns["entity_markup"]["spans"]["1"] == {"tag": "person-", "payload": "Jean"}


# ===========================================================================
# 8. Machine sentinels inside a marked span, per CARRIER.
# ===========================================================================


def test_sentinel_in_a_payload_in_node_text_is_refused(tmp_path):
    """`<person>Jean⟦FNREF_1⟧</person>` would render as
    `[[People/Jean|Jean[^1]]]`, where the footnote closer collides with the
    wikilink closer. Refusing tells the operator to write
    `<person>Jean</person>⟦FNREF_1⟧`, which is what they meant."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(root, p1_text=f"Prose <person>Jean{FN_PH_1}</person> done.")

    payload = assert_refused(run_assemble(root), "entity_markup_span_contains_sentinel")
    assert "FNREF_1" in payload["error"], payload["error"]


def test_sentinel_in_a_payload_in_verse_rendered_is_refused(tmp_path):
    """The same defect one carrier over. The footnote anchor moves out of the
    prose so it stays a single, legitimate book-wide citation."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(
        root,
        p1_text="Prose one, no anchor here.",
        va_rendered=f"Line about <person>Jean{FN_PH_1}</person>",
    )

    assert_refused(run_assemble(root), "entity_markup_span_contains_sentinel")


def test_verse_placeholder_in_a_payload_is_refused(tmp_path):
    """p2 is an ordinary prose node that legitimately carries an INLINE-mount
    verse placeholder, so this is a payload swallowing a sentinel the node
    really does own -- not a misplaced-verse fixture."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(root, p2_text=f"Inline <person>Anne {V_PH_B}</person> carrier.")

    payload = assert_refused(run_assemble(root), "entity_markup_span_contains_sentinel")
    assert "VERSE_vB" in payload["error"], payload["error"]


def test_sentinel_in_a_ref_value_is_refused(tmp_path):
    """The `ref` half, and the one that is invisible without this guard:
    `ref="⟦FNREF_1⟧"` passes _scan_and_validate_sentinels unremarked (footnote
    1 IS valid at this site) and would then be lifted into the span record and
    written verbatim into a markup note's name, ref and `# heading`."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(root, p1_text=f"Prose <person ref=\"{FN_PH_1}\">Jean</person> done.")

    payload = assert_refused(run_assemble(root), "entity_markup_span_contains_sentinel")
    assert "ref" in payload["error"], payload["error"]


def test_verse_placeholder_in_a_ref_value_is_refused(tmp_path):
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(root, p2_text=f"Inline <person ref=\"{V_PH_B}\">Anne</person> carrier.")

    assert_refused(run_assemble(root), "entity_markup_span_contains_sentinel")


def test_a_sentinel_in_a_marked_footnote_definition_does_not_raise(tmp_path):
    """The documented SCOPE LIMIT of the refusal above, pinned as shipped
    behaviour rather than as a universal claim the code does not make:
    build_nodestream strips every sentinel out of a footnote DEFINITION's text
    before this pass ever sees it (Phase 0 policy -- a definition's own nested
    sentinels are stripped, never recursively expanded), so the guard has
    nothing to say there. Nothing leaks; the payload simply arrives already
    stripped."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(root, footnote_text=f"Note about <person>Marc{FN_PH_1}</person> here.")

    assert_ok(run_assemble(root))
    ns = read_nodestream(root)
    assert ns["entity_markup"]["spans"]["1"] == {"tag": "person", "payload": "Marc"}
    assert footnote_text(ns, 1) == "Note about ⟦ENT_1⟧Marc⟦/ENT_1⟧ here."


# ===========================================================================
# 9. Characters the obsidian emission grammar cannot escape.
# ===========================================================================


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("Jean|Anne", id="pipe"),
        pytest.param("Jean]]", id="double-close-bracket"),
        pytest.param("[Jean", id="open-bracket"),
        pytest.param("Jean\nAnne", id="bare-LF"),
        pytest.param("Jean\rAnne", id="bare-CR"),
    ],
)
def test_unsafe_characters_in_a_payload_are_refused(tmp_path, payload):
    """CR and LF are asserted SEPARATELY: a `\\n`-only implementation passes an
    LF-only fixture and still ships a note name broken by a lone CR."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(root, p1_text=f"Prose <person>{payload}</person> {FN_PH_1} done.")

    assert_refused(run_assemble(root), "entity_markup_span_unsafe_text")


@pytest.mark.parametrize(
    "ref",
    [
        pytest.param("jean\nanne", id="ref-bare-LF"),
        pytest.param("jean\ranne", id="ref-bare-CR"),
        pytest.param("jean|anne", id="ref-pipe"),
    ],
)
def test_unsafe_characters_in_a_ref_value_are_refused(tmp_path, ref):
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(root, p1_text=f"Prose <person ref=\"{ref}\">Jean</person> {FN_PH_1} done.")

    assert_refused(run_assemble(root), "entity_markup_span_unsafe_text")


def test_ordinary_punctuation_in_a_payload_is_accepted(tmp_path):
    """The refusal above must be narrow: an apostrophe, a comma and a period
    are ordinary in a printed name and must not be swept up with the four
    characters the emission grammar genuinely cannot carry."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(root, p1_text=f"Prose <person>Jean d'Arc, Jr.</person> {FN_PH_1} done.")

    assert_ok(run_assemble(root))
    ns = read_nodestream(root)
    assert ns["entity_markup"]["spans"]["1"]["payload"] == "Jean d'Arc, Jr."


# ===========================================================================
# 10. A span in a kind:"verse" node's own `text` is never delivered.
# ===========================================================================


def test_span_in_a_verse_nodes_own_text_is_refused(tmp_path):
    """`_render_block` renders a kind:"verse" node from `node["verses"]` alone
    and IGNORES that node's own `text`. A span marked there would be recorded,
    counted and resolved and would still never reach the vault -- a silent
    shortfall in exactly the coverage this feature promises."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(root, vblock_text=f"<person>Jean</person> {V_PH_A}")

    payload = assert_refused(run_assemble(root), "entity_markup_span_unrendered")
    assert "vblockA" in payload["error"], payload["error"]


def test_the_same_markup_in_that_nodes_verse_content_is_accepted(tmp_path):
    """The other half of the pair: the refusal is about the CARRIER, not about
    verse nodes as such."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(root, va_rendered="Line about <person>Jean</person>")

    assert_ok(run_assemble(root))
    ns = read_nodestream(root)
    assert node_by_id(ns, "vblockA")["verses"][0]["content"]["rendered"] == (
        "Line about ⟦ENT_1⟧Jean⟦/ENT_1⟧"
    )


# ===========================================================================
# 11. `index_from: markup` under a target that cannot consume the spans.
# ===========================================================================


def test_index_from_markup_under_a_non_obsidian_target_is_fatal(tmp_path):
    """No other shipped adapter consumes the recorded spans, and silently
    degrading to `strip` would hand the operator an index they asked for and
    did not get."""
    root = make_root(
        tmp_path, output_target="custom", entity_markup=PERSON_PLACE_INDEX
    )
    build_book(root)

    payload = assert_refused(
        run_assemble(root), "entity_markup_index_unsupported_target"
    )
    assert "custom" in payload["error"], payload["error"]


def test_strip_mode_is_allowed_under_a_non_obsidian_target(tmp_path):
    """Removing the elements is target-neutral -- only the INDEX needs an
    adapter that consumes it -- so the refusal above must not spread to strip.

    Asserting only "the run did not fail for an entity_markup reason" would be
    vacuous: a run that never scanned anything passes that too. So this ships
    a real no-op custom renderer, requires the run to SUCCEED, and reads the
    persisted NodeStream: the element is gone, the payload is in the prose,
    and there is no `entity_markup` key -- exactly strip mode's contract, now
    proved under a target that is not obsidian."""
    root = make_root(
        tmp_path, output_target="custom", entity_markup=PERSON_PLACE,
        custom_renderer_path="noop_renderer.py",
    )
    custom_dir = root / "scripts" / "custom_renderers"
    custom_dir.mkdir(parents=True)
    (custom_dir / "noop_renderer.py").write_text(
        "def render(nodestream, canon, profile, out_dir):\n"
        "    out_dir.mkdir(parents=True, exist_ok=True)\n"
        "    (out_dir / 'book.txt').write_text('ok', encoding='utf-8')\n"
        "    return {'written': ['book.txt'], 'kind': 'file'}\n",
        encoding="utf-8",
    )
    build_book(root, p1_text=f"Prose <person>Jean</person> {FN_PH_1} done.")

    proc = run_assemble(root)
    payload = assert_ok(proc)
    assert payload["entity_markup"]["mode"] == "strip", payload
    assert payload["entity_markup"]["spans"] == 1, payload

    nodestream = read_nodestream(root)
    assert "entity_markup" not in nodestream, sorted(nodestream)
    assert node_by_id(nodestream, "p1")["text"] == f"Prose Jean {FN_PH_1} done."


def test_strip_mode_accepts_a_payload_and_ref_index_mode_would_refuse(tmp_path):
    """The three renderer-facing refusals are index-gated ON PURPOSE, and that
    relaxation needs its own pin or it silently becomes a refusal again.

    Strip mode deletes the element and puts the payload back byte for byte --
    it emits no wikilink alias, no note name and no `# H1` -- so a bracket, a
    pipe and a footnote sentinel inside a marked run are ordinary text here.
    All three sit in the payload and are asserted to survive INTO the prose
    rather than merely to avoid a refusal; the `ref` carries a bracket and a
    pipe of its own, which index mode also refuses. The ref cannot carry the
    footnote sentinel as well -- this book anchors ⟦FNREF_1⟧ exactly once, and
    a second use is `duplicate_footnote_ref` long before this pass runs."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE)
    payload_text = f"Jean [le sage] | {FN_PH_1}"
    build_book(
        root,
        p1_text=f'Prose <person ref="a|b [x]">{payload_text}</person> done.',
    )

    proc = run_assemble(root)
    result = assert_ok(proc)
    assert result["entity_markup"]["mode"] == "strip", result

    nodestream = read_nodestream(root)
    assert "entity_markup" not in nodestream, sorted(nodestream)
    assert node_by_id(nodestream, "p1")["text"] == f"Prose {payload_text} done."


def test_index_mode_still_refuses_the_text_strip_mode_accepts(tmp_path):
    """The other half of the pin above -- without it, "strip accepts X" would
    pass just as well if index accepted X too, and the gating would be
    untested in the direction that matters."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(root, p1_text=f"Prose <person>Jean [le sage]</person> {FN_PH_1} done.")

    assert_refused(run_assemble(root), "entity_markup_span_unsafe_text")


# ===========================================================================
# 12. Everything the operator did NOT declare is left alone.
# ===========================================================================


@pytest.mark.parametrize(
    "block, mode",
    [
        pytest.param(PERSON_PLACE, "strip", id="strip"),
        pytest.param(PERSON_PLACE_INDEX, "index", id="index"),
    ],
)
def test_an_undeclared_angle_bracket_run_survives_verbatim(tmp_path, block, mode):
    """This is a declared-vocabulary pass, never a generic tag stripper and
    never a detector of undeclared markup."""
    root = make_root(tmp_path, entity_markup=block)
    untouched = f"Prose <b>bold</b> and 3 < 5 and <city>Kiev</city> {FN_PH_1} done."
    build_book(root, p1_text=untouched)

    payload = assert_ok(run_assemble(root))
    ns = read_nodestream(root)
    assert node_by_id(ns, "p1")["text"] == untouched
    assert payload["entity_markup"]["mode"] == mode
    assert payload["entity_markup"]["spans"] == 0


def test_zero_markup_under_a_declared_block_reports_a_visible_zero(tmp_path):
    """Not a refusal -- a book may genuinely carry none -- but the zero has to
    be VISIBLE rather than indistinguishable from a scan that never ran, which
    is why the counts are emitted even when they are all zero."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE_INDEX)
    build_book(root)

    payload = assert_ok(run_assemble(root))
    counts = payload["entity_markup"]
    assert counts["mode"] == "index"
    assert counts["spans"] == 0
    assert counts["tags"] == {"person": 0, "place": 0}
    assert counts["strings_scanned"] > 0, counts
    # `index` mode still declares the key, with an empty span table -- the
    # renderer's own coverage identity compares against len(spans).
    assert read_nodestream(root)["entity_markup"] == {"spans": {}}


# ===========================================================================
# 13. The two-copy `_entity_markup_mode` contract (plan SS4).
#
# The two copies are INDEPENDENT recomputations from the same profile fields,
# never imported from one another -- the `mentions_section` trio's precedent.
# Byte-identical text is not achievable: this copy RAISES on the
# unsupported-target row and a renderer has no such vocabulary, so
# render_obsidian.py reports that row as a fourth VALUE. What is enforced here
# is therefore not source identity but the two things that actually rot
# silently: a THIRD copy appearing, and a docstring that stops naming the
# divergence. What keeps duplicated predicates honest in this plugin is
# ENFORCEMENT, not discipline -- `classify_ever_converged_sentinel`'s own
# docstring records that a remembered convention rots while a test that fails
# loudly does not. This pair lives in the ASSEMBLE suite by agreement with the
# render side, whose suite is render-only.
# ===========================================================================


def _mode_predicate_docstring(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    functions = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.FunctionDef) and node.name == "_entity_markup_mode"
    ]
    assert len(functions) == 1, (
        f"{path.name} declares {len(functions)} `_entity_markup_mode` definitions, "
        f"expected exactly 1"
    )
    doc = ast.get_docstring(functions[0])
    assert doc, f"{path.name}'s `_entity_markup_mode` carries no docstring"
    return doc


def test_exactly_these_two_scripts_carry_the_mode_predicate():
    """Fails when a THIRD copy appears or one of the two goes away. Two copies
    that agree are a duplication the plan accepted; a third nobody registered
    is the drift the `mentions_section`/sentinel-predicate precedents both
    exist to catch."""
    carriers = sorted(
        path.name
        for path in SCRIPTS_SRC_DIR.glob("*.py")
        if "def _entity_markup_mode(" in path.read_text(encoding="utf-8")
    )
    assert carriers == ["assemble.py", "render_obsidian.py"], carriers


@pytest.mark.parametrize(
    "path, other",
    [
        pytest.param(ASSEMBLE_SRC, "render_obsidian.py", id="assemble"),
        pytest.param(RENDER_OBSIDIAN_SRC, "assemble.py", id="render_obsidian"),
    ],
)
def test_each_mode_predicate_docstring_names_the_other_copy_and_the_divergence(path, other):
    """Plan SS4 requires BOTH docstrings to name the other copy and the one
    divergence between them. This is the cheapest thing that can go wrong and
    the most expensive to notice: a maintainer who edits one copy is told about
    the other only by that prose, and prose that quietly stops being true reads
    exactly like prose that is."""
    doc = _mode_predicate_docstring(path)
    assert other in doc, (
        f"{path.name}'s `_entity_markup_mode` docstring no longer names its "
        f"counterpart in {other}"
    )
    for marker in ("index_unsupported_target", "entity_markup_config_invalid"):
        assert marker in doc, (
            f"{path.name}'s `_entity_markup_mode` docstring no longer names the "
            f"{marker!r} divergence between the two copies (one raises where "
            f"the other reports a value)"
        )


# The docstring pins above are prose about behaviour. This is the behaviour:
# both copies resolved against the SAME profiles, in one subprocess, so a
# maintainer who edits one and not the other gets a RED here and not only a
# docstring complaint. `sys.path` is the shipped scripts directory, exactly
# how assemble.py reaches its own siblings at runtime.
_MODE_AGREEMENT_MATRIX = [
    # (profile, expected assemble answer, expected render answer)
    # `assemble` answers are either a mode string or "raise:<reason>".
    ({"output": {"target": "obsidian"}}, "off", "off"),
    ({"output": {"target": "obsidian",
                 "entity_markup": {"tags": ["person"]}}}, "strip", "strip"),
    ({"output": {"target": "obsidian",
                 "entity_markup": {"tags": ["person"], "index_from": "canon"}}},
     "strip", "strip"),
    ({"output": {"target": "obsidian",
                 "entity_markup": {"tags": ["person"], "index_from": "markup"}}},
     "index", "index"),
    # Divergence 1 -- unsupported target: assemble refuses, the renderer stays inert.
    ({"output": {"target": "custom",
                 "entity_markup": {"tags": ["person"], "index_from": "markup"}}},
     "raise:entity_markup_index_unsupported_target", "index_unsupported_target"),
    # Divergence category 2 -- ANY block assemble's validator rejects. The
    # renderer does not validate: it reads two fields and never looks at
    # `tags`, so its answer is whatever those two reads produce. Both ends of
    # that range are pinned, because "off" alone would read like a rule.
    ({"output": {"target": "obsidian", "entity_markup": "person"}},
     "raise:entity_markup_config_invalid", "off"),
    ({"output": {"target": "obsidian",
                 "entity_markup": {"tags": [], "index_from": "markup"}}},
     "raise:entity_markup_config_invalid", "index"),
    ({"output": {"target": "obsidian",
                 "entity_markup": {"tags": "person", "index_from": "markup"}}},
     "raise:entity_markup_config_invalid", "index"),
    ({"output": {"target": "obsidian",
                 "entity_markup": {"tags": ["person"], "nope": 1}}},
     "raise:entity_markup_config_invalid", "strip"),
]

_MODE_AGREEMENT_PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import assemble, render_obsidian

out = []
for profile in json.loads(sys.argv[2]):
    try:
        a = assemble._entity_markup_mode(profile)
    except assemble.AssembleError as exc:
        a = "raise:" + (getattr(exc, "reason", None) or "?")
    out.append([a, render_obsidian._entity_markup_mode(profile)])
print(json.dumps(out))
"""


def test_the_two_mode_predicates_resolve_every_profile_the_same_way(tmp_path):
    """The behavioural half of the two-copy contract. Docstring markers rot
    loudly; a predicate that silently starts answering `strip` where its twin
    answers `index` does not, and it would ship an index the operator asked
    for and did not get -- or spans nothing consumes. Every row of the mode
    table gets asserted here, and so does the RULE every divergence obeys:
    assemble REFUSES wherever the renderer resolves, never the reverse, and
    never a disagreement about which mode a VALID profile gets. The second
    divergence category is open-ended -- the renderer validates nothing, so
    an invalid block can resolve there to `off`, `strip` or `index` -- and
    all three of those are pinned, because pinning only `off` would read
    like a rule the code does not have.

    What is NOT asserted here is that this list is every profile the two
    could ever disagree on: it cannot be, since one side's answer is defined
    by a validator with its own open vocabulary. What IS asserted is the
    direction, which is the property the safety argument rests on."""
    probe = tmp_path / "probe.py"
    probe.write_text(_MODE_AGREEMENT_PROBE, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(probe), str(SCRIPTS_SRC_DIR),
         json.dumps([row[0] for row in _MODE_AGREEMENT_MATRIX])],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    got = json.loads(proc.stdout)
    expected = [[row[1], row[2]] for row in _MODE_AGREEMENT_MATRIX]
    for (profile, a, _r), (got_a, got_r) in zip(_MODE_AGREEMENT_MATRIX, got):
        if got_a == got_r:
            continue
        assert got_a.startswith("raise:"), (
            f"the two copies disagree WITHOUT assemble refusing, which is the "
            f"one shape the divergence rule excludes: {json.dumps(profile)} -> "
            f"assemble={got_a!r} render={got_r!r}"
        )
    assert got == expected, (
        "the two `_entity_markup_mode` copies no longer agree row for row.\n"
        + "\n".join(
            f"  {json.dumps(row[0], sort_keys=True)}\n"
            f"    expected assemble={row[1]!r} render={row[2]!r}\n"
            f"    got      assemble={g[0]!r} render={g[1]!r}"
            for row, g in zip(_MODE_AGREEMENT_MATRIX, got)
            if [row[1], row[2]] != g
        )
    )


# ===========================================================================
# 15. The three renderer-facing refusals are INDEX-MODE ONLY. Strip mode
#     deletes the element and puts the payload back byte-for-byte -- it emits
#     no wikilink alias and no note name, records nothing, and promises no
#     coverage -- so a bracket, a pipe, a line break, a machine sentinel or a
#     verse-node span is ordinary input there. Refusing it would be a false
#     RED on text this mode handles correctly.
# ===========================================================================


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("Jean [le Bon]", id="payload-brackets"),
        pytest.param("Jean|Anne", id="payload-pipe"),
        pytest.param("Jean\nAnne", id="payload-bare-LF"),
        pytest.param("Jean\rAnne", id="payload-bare-CR"),
    ],
)
def test_strip_mode_accepts_a_payload_the_emission_grammar_could_not_carry(tmp_path, payload):
    root = make_root(tmp_path, entity_markup=PERSON_PLACE)
    build_book(root, p1_text=f"Prose <person>{payload}</person> {FN_PH_1} done.")

    assert_ok(run_assemble(root))
    ns = read_nodestream(root)
    assert node_by_id(ns, "p1")["text"] == f"Prose {payload} {FN_PH_1} done."
    assert "entity_markup" not in ns, sorted(ns)


def test_strip_mode_accepts_a_machine_sentinel_inside_a_marked_payload(tmp_path):
    """The index-mode refusal exists because `<person>X⟦FNREF_1⟧</person>`
    would render as `[[People/X|X[^1]]]`, whose footnote closer collides with
    the wikilink closer. Strip mode emits no wikilink at all, so the same
    input is simply a name with a footnote anchor after it."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE)
    build_book(root, p1_text=f"Prose <person>Jean{FN_PH_1}</person> done.")

    assert_ok(run_assemble(root))
    assert node_by_id(read_nodestream(root), "p1")["text"] == f"Prose Jean{FN_PH_1} done."


def test_strip_mode_accepts_a_span_in_a_verse_nodes_own_text(tmp_path):
    """Nothing is recorded in strip mode, so there is no coverage claim for an
    unrendered span to falsify -- the node's `text` is dropped by the renderer
    either way, exactly as it is on a project that declares no markup."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE)
    build_book(root, vblock_text=f"<person>Jean</person> {V_PH_A}")

    assert_ok(run_assemble(root))
    assert node_by_id(read_nodestream(root), "vblockA")["text"] == f"Jean {V_PH_A}"


def test_strip_mode_still_refuses_malformed_markup(tmp_path):
    """The mode gate is narrow: pairing and the declared-tag-token guard are
    about the MARKUP itself and fire in both modes."""
    root = make_root(tmp_path, entity_markup=PERSON_PLACE)
    build_book(root, p1_text=f"Prose <person>Jean {FN_PH_1} done.")

    assert_refused(run_assemble(root), "entity_markup_malformed")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
