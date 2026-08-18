"""tests/_registry_fixture.py -- a real durable root for the W9r person-registry
pass (#550), shared by person_registry_prep.test.py and
person_registry_build.test.py.

Not a mock and not a hand-shaped approximation of what the pipeline emits: this
builds a manifest, a canon, a canon_senses sidecar with REAL matcher-verifiable
evidence offsets, an assembled NodeStream, a ledger whose `reviewed_draft_sha1`
is computed by the plugin's own `draft_sha1.draft_content_sha1`, and a profile
-- then runs the SHIPPED `person_registry.py` against it as a subprocess, the
same way an operator does. A fixture that fed the script its own idea of the
inputs would pass while the real join was broken.

The corpus is deliberately small but carries every shape the pass has to
handle: a name with occurrences in two blocks AND a footnote, a canon entry
declared not identity-bearing, an adjudicated homonym split that exists ONLY in
canon_senses.json (never in canon.json's entries{} -- that is the whole point of
the sidecar), a review_queue form queued twice with two different notes, and a
target text containing `Johnson`, so a boundary-guarded count of `John` is
distinguishable from a substring one.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPT = ASSETS / "scripts" / "person_registry.py"

BLOCK_1 = "Jean était le fils de Paul, et il vivait à Tulle."
BLOCK_2 = "Marie était la femme de Jean. Jean Valjean vint plus tard."
# `Jean` stands alone here, not as `Jean de Tulle`: a particle-joined form is
# ONE name to the production matcher, so a footnote written that way yields no
# `Jean` occurrence at all and the footnote origin would never be exercised.
FOOTNOTE_1 = "Jean, malade, mort en 1830."
VERSE_1 = "Jean, frère de Marie, chantait."
# TWO occurrences of one form in ONE container, far apart: the shape that
# distinguishes a per-occurrence context from the first span repeated.
BLOCK_3 = ("Paul parla le premier, dans la grande salle, devant tous les siens. "
           "Bien plus tard, dans une autre ville, Paul revint seul.")
NODE_1 = "John was the son of Paul, and he lived in Tulle."
NODE_2 = "Mary was John's wife. John came later, and Johnson did not."
NODE_3 = ("Paul spoke first, in the great hall, before all his own. "
          "Much later, in another town, Paul came back alone.")
FOOTNOTE_TEXT = "John of Tulle, died 1830."
# The embedded verse's delivered halves. Both reach the vault when the policy
# emits both, so a name in each is printed twice -- which is what the registry
# counts, and what a corpus built from node text alone would miss.
VERSE_PLACEHOLDER = "⟦VERSE_V001_deadbeef⟧"
VERSE_RENDERED = "John, brother of Mary, sang."
VERSE_GLOSS = "John, brother of Mary, was singing."

LOC_1 = {"origin": "block", "block": "PARA:seg01:0001"}
LOC_2 = {"origin": "block", "block": "PARA:seg01:0002"}
LOC_FN = {"origin": "footnote", "footnote_n": 1}
LOC_VERSE = {"origin": "embedded_verse", "vid": "V001"}


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def build_root(tmp_path: Path) -> Path:
    """Materialize a durable root the shipped script can actually run against."""
    root = tmp_path / "durable"
    for sub in ("scripts", "schemas", "languages", "segments", "runs",
                "out/.assembled", ".claude/literary-translator"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    for src in (ASSETS / "scripts").glob("*.py"):
        shutil.copy2(src, root / "scripts" / src.name)
    for src in (ASSETS / "schemas").glob("*.json"):
        shutil.copy2(src, root / "schemas" / src.name)
    for src in (ASSETS / "languages").glob("*"):
        if src.is_file():
            shutil.copy2(src, root / "languages" / src.name)

    blocks = {
        "PARA:seg01:0001": {"id": "PARA:seg01:0001", "type": "PARA", "seg": "seg01",
                            "order_index": 1, "plain_text": BLOCK_1, "sha1": "a" * 40},
        "PARA:seg01:0002": {"id": "PARA:seg01:0002", "type": "PARA", "seg": "seg01",
                            "order_index": 2, "plain_text": BLOCK_2, "sha1": "b" * 40},
        "PARA:seg01:0003": {"id": "PARA:seg01:0003", "type": "PARA", "seg": "seg01",
                            "order_index": 3, "plain_text": BLOCK_3, "sha1": "e" * 40},
        "FN:1": {"id": "FN:1", "type": "FN", "seg": None, "order_index": 4,
                 "plain_text": FOOTNOTE_1, "sha1": "c" * 40},
    }
    _write(root / "manifest.json", {
        "blocks": blocks,
        "spine": [],
        "segments": [{"seg": "seg01", "kind": "body",
                      "block_ids": ["PARA:seg01:0001", "PARA:seg01:0002", "PARA:seg01:0003"],
                      "word_count": 40}],
        "footnotes": [{"n": 1, "anchor_block": "PARA:seg01:0001", "anchor_seg": "seg01",
                       "def_block": "FN:1"}],
        "frontback": [],
        # One embedded verse, so the origin-aware locator has a container that
        # is NOT its parent block: the parent block carries only the
        # placeholder, the verse's prose lives here. A block-only locator
        # cannot verify a quote from it, and would happily accept a quote
        # lifted from the parent block's unrelated prose instead.
        # `mount: "embedded"` verbatim -- the extractor's own classification,
        # and the exact string occurrence_targets keys the embedded-verse scan
        # off ("anything else -> block", per the segpack schema).
        "verse": {"store": [{"vid": "V001", "placeholder": VERSE_PLACEHOLDER,
                             "context": "body", "mount": "embedded",
                             "parent_block": "PARA:seg01:0002",
                             "plain_text": VERSE_1, "sha1": "f" * 40}]},
    })

    _write(root / "canon.json", {
        "entries": {
            "Jean": {"source_form": "Jean", "is_proper_name": True, "canonical_target_form": "John",
                     "basis": "established", "source": "https://example.org/j",
                     "confidence": "high", "category": "person"},
            "Paul": {"source_form": "Paul", "is_proper_name": True, "canonical_target_form": "Paul",
                     "basis": "transliterated", "confidence": "high", "category": "person"},
            "Marie": {"source_form": "Marie", "is_proper_name": True, "canonical_target_form": "Mary",
                      "basis": "transliterated", "confidence": "high", "category": "person"},
            "Tulle": {"source_form": "Tulle", "is_proper_name": True, "canonical_target_form": "Tulle",
                      "basis": "transliterated", "confidence": "high", "category": "place"},
            "Le Livre": {"source_form": "Le Livre", "is_proper_name": False,
                         "canonical_target_form": "the book", "basis": "not_a_name",
                         "confidence": "high"},
        },
        # Queued TWICE for two different reasons -- the schema does not require
        # uniqueness here and the merge path appends whenever the whole object
        # differs, so both notes must survive into one prep unit.
        "review_queue": [
            {"source_form": "Bernard", "is_proper_name": True, "disposition": "review_queue",
             "note": "two bearers in the source, unresolved"},
            {"source_form": "Bernard", "is_proper_name": True, "disposition": "review_queue",
             "note": "SOURCE_UNAVAILABLE: no citable form"},
        ],
        "generation_hashes": {"particle_config_hash": "d" * 40, "derivation_bundle_hash": "e" * 40},
    })

    start = BLOCK_2.index("Jean Valjean")
    evidence = {
        "block": "PARA:seg01:0002", "seg": "seg01",
        "char_start": start, "char_end": start + len("Jean Valjean"),
        "context_start": 0, "context_end": len(BLOCK_2),
        "sha256": hashlib.sha256(BLOCK_2.encode("utf-8")).hexdigest(),
    }
    _write(root / "canon_senses.json", {
        "schema_version": 1,
        "entries_by_source_form": {
            "Jean Valjean": {"senses": [
                {"sense_id": "convict", "disambiguator": "the convict",
                 "index_scope": "narrative", "evidence": dict(evidence)},
                {"sense_id": "mayor", "disambiguator": "the mayor",
                 "index_scope": "narrative", "evidence": dict(evidence)},
            ]}
        },
    })

    _write(root / "out" / ".assembled" / "nodestream.json", {
        "nodes": [
            {"id": "PARA:seg01:0001", "seg": "seg01", "kind": "prose", "raw_type": "PARA",
             "level": None, "order_index": 1, "medium": "prose", "text": NODE_1,
             "fnrefs": [1], "verses": []},
            {"id": "PARA:seg01:0002", "seg": "seg01", "kind": "prose", "raw_type": "PARA",
             "level": None, "order_index": 2, "medium": "prose", "text": NODE_2,
             "fnrefs": [],
             # The verse claim the assembler emits: content under `content`,
             # which is also what makes the verse ELIGIBLE for a source-side
             # occurrence (occurrence_targets keys content_by_vid off it).
             "verses": [{"vid": "V001", "placeholder": VERSE_PLACEHOLDER,
                         "content": {"rendered": VERSE_RENDERED,
                                     "literal_gloss": VERSE_GLOSS}}]},
            {"id": "PARA:seg01:0003", "seg": "seg01", "kind": "prose", "raw_type": "PARA",
             "level": None, "order_index": 3, "medium": "prose", "text": NODE_3,
             "fnrefs": [], "verses": []},
        ],
        "footnotes": [{"n": 1, "text": FOOTNOTE_TEXT}],
    })

    _write(root / "segments" / "seg01.draft.json",
           {"seg": "seg01", "blocks": {"PARA:seg01:0001": NODE_1}})

    sys.path.insert(0, str(root / "scripts"))
    try:
        import draft_sha1  # the SOLE sha1 authority for draft files
        sha1 = draft_sha1.draft_content_sha1(root / "segments" / "seg01.draft.json")
    finally:
        sys.path.remove(str(root / "scripts"))
    _write(root / "runs" / "ledger.json",
           {"segments": {"seg01": {"status": "converged", "reviewed_draft_sha1": sha1}}})

    (root / ".claude" / "literary-translator" / "profile.yml").write_text(
        "source:\n  language:\n    particle_config: fr.json\n", encoding="utf-8")
    return root


def run(root: Path, *args: str):
    """Run the SHIPPED script from the plugin path, exactly as W9r documents.
    Returns (returncode, parsed stdout JSON line)."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--durable-root", str(root)],
        capture_output=True, text=True,
    )
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        payload = {"success": False, "reason": "no_json_line", "stdout": proc.stdout,
                   "stderr": proc.stderr}
    return proc.returncode, payload


def prep_digest(root: Path) -> str:
    return json.loads((root / "registry" / "registry_input.json").read_text(encoding="utf-8"))["input_sha256"]


def verdict_doc(root: Path, *, valjean_units=None) -> dict:
    """A plausible Pass A verdict covering every prep unit exactly once."""
    if valjean_units is None:
        valjean_units = [{"source_form": "Jean Valjean", "sense_id": "convict"},
                         {"source_form": "Jean Valjean", "sense_id": "mayor"}]
    return {
        "schema_version": 1,
        "input_sha256": prep_digest(root),
        "people": [
            {"person_id": "john", "display_name": "Jean, son of Paul",
             "units": [{"source_form": "Jean", "sense_id": None}],
             "identity_note": "the son of Paul, of Tulle",
             "identity_status": "confirmed",
             "identity_status_reason": "named with a patronymic and a place",
             "printed_surfaces": ["John"],
             "relations": [{"type": "son_of", "to_person_id": "paul",
                            "evidence": {"quote": "Jean était le fils de Paul", "locator": dict(LOC_1)}}],
             "places": [{"role": "of", "name": "Tulle",
                         "evidence": {"quote": "il vivait à Tulle", "locator": dict(LOC_1)}}],
             "dates": [{"kind": "died", "value": "1830",
                        "evidence": {"quote": "mort en 1830", "locator": dict(LOC_FN)}}]},
            {"person_id": "paul", "display_name": "Paul",
             "units": [{"source_form": "Paul", "sense_id": None}],
             "identity_note": "the father", "identity_status": "contested",
             "identity_status_reason": "one mention only",
             "printed_surfaces": ["Paul"], "relations": [], "places": [], "dates": []},
            {"person_id": "mary", "display_name": "Marie",
             "units": [{"source_form": "Marie", "sense_id": None}],
             "identity_note": "the wife", "identity_status": "contested",
             "identity_status_reason": "one mention only",
             "printed_surfaces": ["Mary"],
             "relations": [{"type": "wife_of", "to_person_id": "john",
                            "evidence": {"quote": "Marie était la femme de Jean", "locator": dict(LOC_2)}}],
             "places": [], "dates": []},
            {"person_id": "valjean", "display_name": "Jean Valjean",
             "units": valjean_units,
             "identity_note": "one man under two senses", "identity_status": "confirmed",
             "identity_status_reason": "assumed",
             "printed_surfaces": [], "relations": [], "places": [], "dates": []},
        ],
        "non_person_forms": [{"unit": {"source_form": "Tulle", "sense_id": None},
                              "kind": "place", "reason": "a town"}],
        "refusals": [{"unit": {"source_form": "Bernard", "sense_id": None},
                      "reason": "two bearers, unresolved"}],
    }


def write_verdict(root: Path, doc: dict) -> None:
    _write(root / "registry" / "registry_verdicts.json", doc)


def write_adjudications(root: Path, *, refuse_person_ids=(), refuse=None) -> dict:
    """Affirm every claim except the person claims of `refuse_person_ids`, and
    except any claim the optional `refuse(claim)` predicate rejects."""
    claims = json.loads((root / "registry" / "registry_claims.json").read_text(encoding="utf-8"))
    rows = []
    for claim in claims["claims"]:
        rejected = (claim["kind"] == "person" and claim["person_id"] in refuse_person_ids) \
            or bool(refuse and refuse(claim))
        rows.append({"claim_id": claim["claim_id"], "affirmed": not rejected,
                     "reason": "not established by the contexts shown" if rejected
                               else "the evidence states it"})
    doc = {"schema_version": 1, "input_sha256": claims["input_sha256"],
           "claims_sha256": claims["claims_sha256"], "adjudications": rows}
    _write(root / "registry" / "registry_adjudications.json", doc)
    return doc


def registry(root: Path) -> dict:
    return json.loads((root / "registry" / "person_registry.json").read_text(encoding="utf-8"))
