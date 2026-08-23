#!/usr/bin/env python3
"""person_registry.py -- W9r, the opt-in person-registry pass (#550).

Some books are translated FOR something other than the translation. When the
deliverable a project's owner actually wants is genealogy, the question they
need answered is "how many distinct people are in this book, which name forms
are the same person, what does the book print them as, and how are they
related" -- and nothing else in this plugin answers it. `canon.json` is a 1:1
name-form -> target-form dictionary with no entity model, by design.

This pass consolidates what the pipeline already produced into a person-keyed
registry, and writes NEW artifacts only. It never touches `canon.json`
(whose `entries{}` feed `cache_key.py::compute_used_terms_hash`, so an edit
there re-stales every converged unit), never touches the rendered vault, and
gates nothing downstream.

## Opt-in means the operator runs it

There is deliberately NO profile knob. W9r is a post-delivery operator tool,
not a link in an automatic chain: a project that wants a registry runs this
script, and a project that does not never invokes it. A `profile.yml` flag
would additionally have meant editing `assets/schemas/profile.schema.json`,
whose copy under `${durable_root}/schemas/` is hashed by
`resume_setup.py::_schemas_dir_hash` into `input_digest` -- costing EVERY
project its resume identity to gate a step nothing auto-runs.

## This script is in NO bundle

Not `cache_key.PLUGIN_BUNDLE_MEMBERS`, not `DERIVATION_BUNDLE_MEMBERS`, not
`scaffold_setup.ORCHESTRATION_BUNDLE_MEMBERS`. It runs after convergence and
decides nothing about what is dispatched or accepted, so its bytes must never
re-stale a finished book. Do not "helpfully" register it: those tuples are
literal name allowlists, and nothing hashes `${durable_root}/scripts/` as a
directory, so a non-adopting project pays exactly one inert file for this
feature and no cache key moves. Its three schemas live under
`assets/schemas/registry/` for the mirror-image reason -- SKILL.md's Step 0a
copy pass globs `assets/schemas/*.json` and `_schemas_dir_hash` globs
`${durable_root}/schemas/*.schema.json`, both NON-recursively, so a schema in
that subdirectory is neither copied nor hashed.

## The one methodological rule this file exists to respect

Deciding whether two name forms denote the same person is INTERPRETATION and
must be an LLM judgement -- never a string matcher, surname heuristic, or
edit-distance rule. A corpus whose naming system fixes identity by patronymic
or place has forms that are ambiguous by construction: on the worked corpus
this pass was generalized from, one spelling of a given name denotes six
different men. A "similar names are one person" rule merges them silently, and
its failure looks exactly like success.

So this script decides NO identity. It reads artifacts, calls the production
occurrence engine, counts strings, validates schemas, verifies quotes exist
where they claim to, joins adjudications to claims, and emits files. Every
identity judgement is made by a model, and every such judgement is then judged
AGAIN by a second, freshly dispatched model call that sees each claim in
isolation -- because a verbatim-quote check proves a sentence exists, never
that it says what the claim says. A model can cite a real sentence ("David
visited Isaac in Warsaw") and attach `son_of: Isaac`; only a reader can catch
that.

## Three modes, two model calls between them

    python3 person_registry.py --prep    [--durable-root PATH] [--plugin-root PATH]
    #   -> registry/registry_input.json      (the whole cast, with evidence)
    #   ... Pass A: one model call over the whole cast
    #   -> registry/registry_verdicts.json   (written by the model)
    python3 person_registry.py --claims  [...]
    #   -> registry/registry_claims.json     (each judgement, isolated)
    #   ... Pass B: a FRESH dispatch seeing only its instructions + that file
    #   -> registry/registry_adjudications.json   (written by the model)
    python3 person_registry.py --build   [...]
    #   -> registry/person_registry.json + registry/PEOPLE.md

Exit 0 on success, 1 on a data/verdict rejection, 2 on a usage or precondition
failure. One JSON line on stdout, human detail on stderr, per house style.

See `references/person-registry.md` for the full contract and SKILL.md's W9r
step for how to run it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

# Importing a sibling module writes scripts/__pycache__/*.pyc. Several
# entrypoints here promise not to write anything (cache_key.py) or promise ZERO
# filesystem writes in dry-run (backfill_resume_gate_ack.py), so the whole set
# opts out uniformly rather than case by case.
sys.dont_write_bytecode = True


# --- the shared one-line JSON serialiser (#369) -----------------------------
# Loaded by EXACT PATH, never `import json_stdout`. A bare sibling import
# resolves through the global sys.modules cache regardless of which staged copy
# the CALLER intended, so one process that stages several durable roots would
# bind the FIRST root's copy for all of them. exec_module() opens this file's
# own sibling or raises -- the loud failure the staging discipline depends on,
# and it needs no cache eviction to get there. `Path(__file__).absolute()`
# rather than `.resolve()`: the unresolved form is what lets a caller's own
# no-follow symlink logic still see the path it was handed.
import importlib.util as _importlib_util

_JSON_STDOUT_PATH = Path(__file__).absolute().parent / "json_stdout.py"
try:
    _json_stdout_spec = _importlib_util.spec_from_file_location(
        "json_stdout", _JSON_STDOUT_PATH
    )
    if _json_stdout_spec is None or _json_stdout_spec.loader is None:
        raise ImportError(f"no loader for {_JSON_STDOUT_PATH}")
    _json_stdout = _importlib_util.module_from_spec(_json_stdout_spec)
    # OSError, not ImportError alone: spec_from_file_location() happily builds a
    # spec for a file that is not there, and it is exec_module() that raises
    # FileNotFoundError when it opens the source.
    _json_stdout_spec.loader.exec_module(_json_stdout)
except (ImportError, OSError) as _json_stdout_exc:  # pragma: no cover - staging error path
    sys.exit(
        f"person_registry.py: cannot load json_stdout.py from {_JSON_STDOUT_PATH} "
        f"({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside person_registry.py under "
        "${durable_root}/scripts/ -- Step 0a's copy pass places it there."
    )

dumps_line = _json_stdout.dumps_line

# ---------------------------------------------------------------------------
# Self-anchoring: this script lives at {durable_root}/scripts/<name>.py when
# copied, and at {plugin_root}/assets/scripts/<name>.py in the plugin tree.
# Both layouts put its siblings (bootstrap_names.py, canon_senses.py,
# occurrence_targets.py, occ_index.py, evidence_verify.py, draft_sha1.py) in
# the same directory, which is what the lazy imports below rely on.
# ---------------------------------------------------------------------------
SCRIPT_FILE = Path(__file__).resolve()
SCRIPTS_DIR = SCRIPT_FILE.parent
DEFAULT_DURABLE_ROOT = SCRIPTS_DIR.parent

# The registry schemas sit beside assets/scripts/ in the PLUGIN tree only --
# never in a durable root (see the module docstring). Running from a durable
# root therefore requires --plugin-root.
DEFAULT_SCHEMA_DIR = SCRIPTS_DIR.parent / "schemas" / "registry"

SENTINEL_RE = re.compile("[⟦⟧]")
# A whole assembly placeholder, brackets and payload. The renderer substitutes
# these with the verse's delivered halves, so the raw token is printed nowhere
# and must not sit in a corpus that counts printed names.
PLACEHOLDER_RE = re.compile("⟦[^⟧]*⟧")

VERDICTS_SCHEMA = "registry-verdicts.schema.json"
ADJUDICATIONS_SCHEMA = "registry-adjudications.schema.json"
REGISTRY_SCHEMA = "person-registry.schema.json"


class RegistryError(Exception):
    """A named, reportable failure. `reason` is the machine-readable slug that
    reaches stdout's JSON line; `code` is the process exit code (1 for a data
    or verdict rejection, 2 for a usage/precondition failure)."""

    def __init__(self, reason: str, message: str, code: int = 1):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.code = code


# ---------------------------------------------------------------------------
# Small deterministic helpers. canonical_json_bytes is byte-identical to
# cache_key.py's own -- duplicated rather than imported, per this project's
# "no shared lib between self-contained scripts" convention and because
# cache_key.py is a bundle member whose import surface is deliberately not
# widened by a non-member.
# ---------------------------------------------------------------------------

def canonical_json_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256_hex(obj) -> str:
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def nfc(s: str) -> str:
    """NFC-normalize. Applied to BOTH a printed surface and the assembled
    target text before counting: a composed and a decomposed spelling of the
    same name are the same name to a reader, and comparing them raw reports a
    printed name as absent."""
    return unicodedata.normalize("NFC", s or "")


def unit_key(source_form: str, sense_id):
    """The pass's unit of identity, as a hashable tuple. NEVER the bare
    spelling: `canon_senses.json` deliberately admits one form with two or
    more senses, and keying on the spelling alone would force two people who
    share a surface into one owner or into a refusal."""
    return (source_form, sense_id)


def unit_obj(key) -> dict:
    return {"source_form": key[0], "sense_id": key[1]}


def read_json(path: Path, what: str, *, required: bool = True):
    if not path.is_file():
        if required:
            raise RegistryError(
                "missing_input", f"{what} not found at {path}", code=2
            )
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RegistryError("unreadable_input", f"could not read {what} at {path}: {exc}", code=2)
    except json.JSONDecodeError as exc:
        raise RegistryError("malformed_input", f"{what} at {path} is not valid JSON: {exc}", code=2)


def emitted_json_text(doc) -> str:
    """The exact text `write_json` writes -- which is what a model reads.

    Kept separate from `canonical_json_bytes` on purpose: that one is the digest
    input (compact, sorted) and is SMALLER than the file. A size guard that
    measures it is measuring a serialization nobody receives, so it passes on a
    document that is over the cap in the only form that exists on disk.
    """
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, doc) -> None:
    write_text(path, emitted_json_text(doc))


# ---------------------------------------------------------------------------
# Dependency + sibling-module resolution.
# ---------------------------------------------------------------------------

def load_validator(schema_dir: Path, name: str):
    """Return a real `jsonschema.Draft202012Validator` for one registry
    schema. Never a hand-rolled shape check: the whole point of these schemas
    is that an authoring or model bug fails LOUD at load time rather than
    silently under-enforcing."""
    try:
        import jsonschema
    except ImportError as exc:
        raise RegistryError(
            "dependency_missing",
            f"the registry pass requires the 'jsonschema' package: {exc}",
            code=2,
        )
    path = schema_dir / name
    if not path.is_file():
        raise RegistryError(
            "schema_not_found",
            f"registry schema {name} not found at {path} -- when running this script from a "
            f"durable root, pass --plugin-root PATH (the registry schemas are deliberately never "
            f"copied into a durable root; see this script's module docstring)",
            code=2,
        )
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError("schema_unreadable", f"could not load {path}: {exc}", code=2)
    return jsonschema.Draft202012Validator(schema)


def validate_or_raise(validator, doc, reason: str, what: str) -> None:
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "(root)"
        raise RegistryError(
            reason,
            f"{what} failed schema validation ({len(errors)} error(s)); first at {where}: {first.message}",
        )


def import_siblings():
    """Import the sibling modules this pass reuses rather than reimplements.
    Lazy and in one place so a failure names all of them together.

    Every one of these is an authority this script must NOT duplicate:
    `occurrence_targets.build` is the source-anchored occurrence engine
    `assemble.py` itself uses for the `## Mentions` appendix;
    `occ_index.production_occurrences` is the production matcher whose spans
    are the only valid evidence offsets; `evidence_verify.verify_senses` is
    the real verifier for `canon_senses.json` evidence (its loader checks
    structure only); `draft_sha1.draft_content_sha1` is the sole sha1
    authority for draft files."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import bootstrap_names
        import canon_senses
        import draft_sha1
        import evidence_verify
        import occ_index
        import occurrence_targets
    except ImportError as exc:
        raise RegistryError(
            "sibling_import_failed",
            f"could not import the pipeline modules this pass reuses from {SCRIPTS_DIR}: {exc}",
            code=2,
        )
    except SystemExit as exc:
        raise RegistryError(
            "sibling_preflight_failed",
            f"a pipeline module halted during its own dependency preflight: {exc}",
            code=2,
        )
    finally:
        try:
            sys.path.remove(str(SCRIPTS_DIR))
        except ValueError:
            pass
    return {
        "bootstrap_names": bootstrap_names,
        "canon_senses": canon_senses,
        "draft_sha1": draft_sha1,
        "evidence_verify": evidence_verify,
        "occ_index": occ_index,
        "occurrence_targets": occurrence_targets,
    }


def load_profile(durable_root: Path, explicit: str | None = None) -> dict:
    """Read the project's profile for the ONE field this pass needs:
    `source.language.particle_config`, which resolves the LanguageConfig the
    production matcher is parameterized by. Deliberately a plain YAML read
    with no schema pass -- this script adds no profile key and validates none;
    Step 0 already owns that."""
    try:
        import yaml
    except ImportError as exc:
        raise RegistryError(
            "dependency_missing", f"the registry pass requires the 'pyyaml' package: {exc}", code=2
        )
    candidates = (
        [Path(explicit)]
        if explicit
        else [
            durable_root / ".claude" / "literary-translator" / "profile.yml",
            durable_root.parent / ".claude" / "literary-translator" / "profile.yml",
            Path.cwd() / ".claude" / "literary-translator" / "profile.yml",
        ]
    )
    for path in candidates:
        if path.is_file():
            try:
                return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except OSError as exc:
                raise RegistryError("unreadable_input", f"could not read profile at {path}: {exc}", code=2)
            except yaml.YAMLError as exc:
                raise RegistryError("malformed_input", f"profile at {path} is not valid YAML: {exc}", code=2)
    raise RegistryError(
        "missing_input",
        "profile.yml not found; looked in " + ", ".join(str(p) for p in candidates),
        code=2,
    )


# ---------------------------------------------------------------------------
# Container resolution -- where a quote is verified, per origin.
#
# The three origins live in three DIFFERENT containers, and a block-only
# locator is unverifiable for two of them: an embedded verse's parent block
# carries only the placeholder, not the verse's prose, so a block-only rule
# would both reject a legitimate verse quote and admit a quote lifted from
# unrelated parent-block text.
# ---------------------------------------------------------------------------

def resolve_container(manifest: dict, locator: dict):
    """Return (text, description) for one locator, or raise RegistryError.

    An embedded verse is keyed by bare `vid`, which is correct and needs no
    composite: `manifest.verse.store[]`'s vid space is GLOBALLY unique
    book-wide and `assemble.py` raises on a duplicate -- a deliberately
    stronger guarantee than segpack's own per-segment vid (the reason the
    cross-block duplicate check elsewhere keys on the placeholder string)."""
    origin = (locator or {}).get("origin")
    blocks = (manifest or {}).get("blocks") or {}

    if origin == "block":
        block_id = locator.get("block")
        block = blocks.get(block_id)
        if not isinstance(block, dict):
            raise RegistryError("locator_unresolved", f"manifest has no block {block_id!r}")
        return block.get("plain_text") or "", f"block {block_id}"

    if origin == "embedded_verse":
        vid = locator.get("vid")
        store = ((manifest or {}).get("verse") or {}).get("store") or []
        matches = [v for v in store if isinstance(v, dict) and v.get("vid") == vid]
        if len(matches) != 1:
            raise RegistryError(
                "locator_unresolved",
                f"manifest.verse.store has {len(matches)} entries for vid {vid!r}, expected exactly 1",
            )
        return matches[0].get("plain_text") or "", f"verse {vid}"

    if origin == "footnote":
        n = locator.get("footnote_n")
        entries = [f for f in ((manifest or {}).get("footnotes") or []) if isinstance(f, dict) and f.get("n") == n]
        if len(entries) != 1:
            raise RegistryError(
                "locator_unresolved",
                f"manifest.footnotes has {len(entries)} entries for n={n!r}, expected exactly 1",
            )
        def_block = entries[0].get("def_block")
        block = blocks.get(def_block)
        if not isinstance(block, dict):
            raise RegistryError(
                "locator_unresolved", f"footnote {n} names def_block {def_block!r}, which the manifest lacks"
            )
        return block.get("plain_text") or "", f"footnote {n} (block {def_block})"

    raise RegistryError("locator_unresolved", f"unknown locator origin {origin!r}")


def locator_for_record(rec: dict) -> dict:
    """Project one production occurrence record into the locator shape the
    model is asked to cite. `origin` and the per-origin key both come from
    the engine's own record -- never invented here."""
    origin = rec.get("origin")
    if origin == "embedded_verse":
        return {"origin": origin, "vid": rec.get("vid")}
    if origin == "footnote":
        return {"origin": origin, "footnote_n": rec.get("footnote_n")}
    return {"origin": "block", "block": rec.get("source_block")}


# ---------------------------------------------------------------------------
# --prep
# ---------------------------------------------------------------------------

def check_assembly_currency(manifest: dict, nodestream: dict, ledger: dict, durable_root: Path, mods) -> None:
    """Refuse to build a registry over a stale or partial assembly.

    Presence of `out/.assembled/nodestream.json` proves nothing on its own:
    `render_obsidian.py`'s clean-then-rebuild deliberately PRESERVES dot-
    prefixed entries, so a NodeStream from a run whose scope or drafts have
    since moved survives on disk indefinitely, and counting against it
    produces confidently wrong numbers.

    Two checks, chosen so that neither duplicates `assemble.py`'s own
    accepted-set semantics (which include a machinery-only stale carve-out --
    re-deriving that here would be a second implementation certain to drift):

      1. Every BODY segment in `manifest.segments[]` contributes at least one
         node to the NodeStream. A frontback unit may legitimately contribute
         none (a `disposition: omit` unit becomes no node at all), so only
         `kind == "body"` is required. This catches a partial assembly and a
         scope change.
      2. For every segment the NodeStream DOES carry, the ledger's
         `reviewed_draft_sha1` still equals the current on-disk draft's hash,
         computed by `draft_sha1.draft_content_sha1` -- the sole sha1
         authority for draft files. This catches a draft hand-edited after
         assembly. Assembly itself already refused to admit a segment it did
         not accept, so membership in the NodeStream carries that decision
         forward without this script re-deciding it.

    What it does NOT catch is stated rather than implied, here and in the
    emitted artifact's `assembly_currency: "not_bound"`: a segment revised,
    re-reviewed and re-converged AFTER assembly ran matches the new ledger and
    is still listed in the old NodeStream, so both checks pass over a stale
    target corpus. Binding that would mean persisting per-segment reviewed-
    draft hashes inside the NodeStream -- an `assemble.py` change, and
    `assemble.py` is a PLUGIN_BUNDLE_MEMBER whose bytes re-stale every
    converged segment of every project. Run W9r immediately after W9 instead.
    """
    node_segs = {
        node.get("seg")
        for node in (nodestream or {}).get("nodes") or []
        if isinstance(node, dict) and node.get("seg") is not None
    }

    missing = sorted(
        str(entry.get("seg"))
        for entry in (manifest or {}).get("segments") or []
        if isinstance(entry, dict) and entry.get("kind") == "body" and entry.get("seg") not in node_segs
    )
    if missing:
        raise RegistryError(
            "assembly_incomplete",
            "the assembled NodeStream is missing body segment(s) the manifest declares: "
            + ", ".join(str(m) for m in missing)
            + " -- re-run W9 before W9r",
            code=2,
        )

    # Only segments the manifest DECLARES have a ledger row and a draft. A
    # frontback `decision: regenerate` unit becomes a node carrying its own
    # `FRONTBACK:{id}` seg but is deliberately never in `manifest.segments[]`
    # (assemble.py builds it from `manifest.frontback[]` instead), so demanding
    # a draft for every seg the NodeStream carries would reject a perfectly
    # current book with regenerated front matter.
    declared = {
        entry.get("seg")
        for entry in (manifest or {}).get("segments") or []
        if isinstance(entry, dict)
    }
    ledger_segments = (ledger or {}).get("segments") or {}
    drifted = []
    for seg in sorted(s for s in node_segs if isinstance(s, str) and s in declared):
        record = ledger_segments.get(seg)
        if not isinstance(record, dict):
            drifted.append(f"{seg}: assembled but absent from runs/ledger.json")
            continue
        recorded = record.get("reviewed_draft_sha1")
        draft_path = durable_root / "segments" / f"{seg}.draft.json"
        if not draft_path.is_file():
            drifted.append(f"{seg}: draft missing at {draft_path}")
            continue
        try:
            current = mods["draft_sha1"].draft_content_sha1(draft_path)
        except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
            drifted.append(f"{seg}: draft sha1 could not be computed ({exc})")
            continue
        if recorded != current:
            drifted.append(f"{seg}: draft sha1 {current} != ledger reviewed_draft_sha1 {recorded}")
    if drifted:
        raise RegistryError(
            "assembly_stale",
            "the assembled book no longer matches the drafts on disk: " + "; ".join(drifted)
            + " -- re-run W9 before W9r",
            code=2,
        )


def spread(items: list, cap: int) -> list:
    """Keep at most `cap` items as an EVEN SPREAD across `items`, in order --
    always including the first and last.

    Never the first N. The first N occurrences of a name all come from one
    part of the book, and that is precisely the view under which two men who
    share a spelling look like one person; a spread shows the adjudicator how
    the form is used across the whole work. Deterministic, so the same input
    always yields the same prompt."""
    if cap <= 0 or len(items) <= cap:
        return list(items)
    if cap == 1:
        return [items[0]]
    last = len(items) - 1
    picked = sorted({round(i * last / (cap - 1)) for i in range(cap)})
    return [items[i] for i in picked]


def delivered_footnote_ns(nodestream: dict) -> set:
    """The footnote numbers the renderer actually emits.

    `render_obsidian.py` prints only the footnotes reached through some node's
    `fnrefs`, and `assemble.py` deliberately puts more than that into
    `nodestream["footnotes"]`: a footnote discovered by recursing INTO a
    definition-embedded verse is referenced-only and "NEVER any node's
    `fnrefs`", because that verse is stripped rather than rendered. Counting
    over the raw footnote list would therefore count text no reader is ever
    shown -- a printed-name count for a page that does not exist."""
    delivered = set()
    for node in (nodestream or {}).get("nodes") or []:
        if isinstance(node, dict):
            delivered.update(n for n in (node.get("fnrefs") or []) if n is not None)
    return delivered


def build_target_index(nodestream: dict) -> dict:
    """Where the DELIVERED text of each container lives, keyed the way a
    source-side locator names it.

    A NodeStream node carries the same id as the manifest block it was
    translated from, so a source occurrence's container has an exact target
    counterpart -- which is what lets a context show the model what the book
    actually PRINTS at that mention, rather than asking it to imagine the
    rendering from the canonical form alone."""
    node_text_by_id, verse_text_by_vid, footnote_text_by_n = {}, {}, {}
    for node in (nodestream or {}).get("nodes") or []:
        if not isinstance(node, dict):
            continue
        text = node.get("text") or ""
        for claim in node.get("verses") or []:
            if not isinstance(claim, dict) or claim.get("vid") is None:
                continue
            content = claim.get("content") or {}
            delivered = "\n".join(
                t for t in (content.get("rendered"), content.get("literal_gloss")) if t
            )
            verse_text_by_vid[claim["vid"]] = delivered
            # Substituted IN PLACE, using the node's own placeholder value --
            # the renderer resolves the same placeholder to the same verse. A
            # standalone (`mount: "block"`) verse's node text is NOTHING BUT
            # the placeholder, and its occurrences are reported as block-origin
            # by `occurrence_targets`, so a raw-text index would show both
            # models `⟦VERSE_…⟧` where the delivered rendering should be --
            # the silent under-coverage this pairing exists to prevent.
            if claim.get("placeholder"):
                text = text.replace(claim["placeholder"], delivered)
        if node.get("id") is not None:
            node_text_by_id[node["id"]] = text
    delivered = delivered_footnote_ns(nodestream)
    for fn in (nodestream or {}).get("footnotes") or []:
        if isinstance(fn, dict) and fn.get("n") is not None and fn["n"] in delivered:
            footnote_text_by_n[fn["n"]] = fn.get("text") or ""
    return {
        "node_text_by_id": node_text_by_id,
        "verse_text_by_vid": verse_text_by_vid,
        "footnote_text_by_n": footnote_text_by_n,
    }


def resolve_target_container(target_index: dict, locator):
    """The delivered text of the container a locator names, or None."""
    origin = (locator or {}).get("origin")
    if origin == "block":
        return target_index["node_text_by_id"].get(locator.get("block"))
    if origin == "embedded_verse":
        return target_index["verse_text_by_vid"].get(locator.get("vid"))
    if origin == "footnote":
        return target_index["footnote_text_by_n"].get(locator.get("footnote_n"))
    return None


def target_window(text, canonical_target_form, context_chars):
    """A bounded slice of the delivered text, centred on the canonical target
    form when that string is present.

    The centring is a DISPLAY heuristic and nothing more -- a plain `find` on
    the canonical form, never a decision about what the book prints. Where the
    delivered rendering differs from the canonical form (an inflection, a
    declension) the find fails, the window starts at the container's beginning
    and says so; that flag is itself the interesting signal, since a rendering
    unlike the canonical form is exactly what `printed_surfaces[]` is for."""
    if text is None:
        return None, False
    text = nfc(text)
    half = max(context_chars // 2, 1)
    if canonical_target_form:
        at = text.find(nfc(canonical_target_form))
        if at >= 0:
            end = at + len(nfc(canonical_target_form))
            return text[max(0, at - half):min(len(text), end + half)], True
    return text[:context_chars], False


def build_contexts(source_form, records, manifest, language_config, mods, max_contexts, context_chars,
                   target_index=None, canonical_target_form=None):
    """One bounded context per kept occurrence -- the source container's text
    windowed around that occurrence, PAIRED with the delivered target text of
    the same container.

    The source window is centred on `occ_index.production_occurrences`' own
    span -- the same authority `evidence_verify.py` binds stored evidence
    against -- rather than on a substring search of this script's own devising.
    A container in which the matcher finds no span is still emitted, windowed
    from its start and flagged, so it is never silently dropped.

    Each record is paired with its OWN span, by position within its container.
    `occurrence_targets` emits one location-only record per physical
    occurrence, so two occurrences in one block arrive as two records with
    identical locators; centring both on the first span would show the model
    the same sentence twice, report `contexts_total: 2`, and raise no
    truncation flag -- hiding a second, possibly distinguishing occurrence
    behind a number that says it was shown. That is the merge failure this
    pass exists to prevent, dressed as full coverage."""
    paired = []
    per_container = {}
    for rec in records:
        locator = locator_for_record(rec)
        key = json.dumps(locator, sort_keys=True, ensure_ascii=False)
        index = per_container.get(key, 0)
        per_container[key] = index + 1
        paired.append((rec, locator, index))

    kept = spread(paired, max_contexts)
    half = max(context_chars // 2, 1)
    contexts = []
    for rec, locator, index in kept:
        try:
            text, _ = resolve_container(manifest, locator)
        except RegistryError:
            continue
        spans = mods["occ_index"].production_occurrences(source_form, text, language_config)
        if index < len(spans):
            start, end = spans[index][0], spans[index][1]
            lo = max(0, start - half)
            hi = min(len(text), end + half)
            matched = True
        else:
            # Fewer spans than records for this container: the occurrence
            # engine and the production matcher disagree about it. Windowed
            # from the start and flagged rather than silently re-showing an
            # earlier span as if it were this occurrence.
            lo, hi, matched = 0, min(len(text), context_chars), False
        target_text, centred = target_window(
            resolve_target_container(target_index or {}, locator) if target_index else None,
            canonical_target_form,
            context_chars,
        )
        contexts.append(
            {
                "seg": rec.get("seg"),
                "locator": locator,
                "text": text[lo:hi],
                "window_centred_on_match": matched,
                "target_text": target_text,
                "target_window_centred_on_canonical_form": centred,
            }
        )
    return contexts, len(records), len(records) > len(kept)


def cmd_prep(args, durable_root: Path, schema_dir: Path) -> dict:
    # `schema_dir` is unused here and kept anyway: all three handlers share one
    # signature so `main` can dispatch without branching. --prep validates
    # nothing against a registry schema -- it writes the input the model reads.
    mods = import_siblings()

    manifest = read_json(durable_root / "manifest.json", "manifest.json")
    canon = read_json(durable_root / "canon.json", "canon.json")
    nodestream = read_json(
        durable_root / "out" / ".assembled" / "nodestream.json",
        "the assembled NodeStream (out/.assembled/nodestream.json) -- W9 must run before W9r",
    )
    ledger = read_json(durable_root / "runs" / "ledger.json", "runs/ledger.json")
    profile = load_profile(durable_root, args.profile)

    check_assembly_currency(manifest, nodestream, ledger, durable_root, mods)
    target_index = build_target_index(nodestream)

    particle_config = (((profile.get("source") or {}).get("language") or {}).get("particle_config"))
    if not particle_config:
        raise RegistryError(
            "profile_incomplete",
            "profile source.language.particle_config is required to resolve the production matcher",
            code=2,
        )
    try:
        language_config = mods["bootstrap_names"].load_language_config(particle_config)
    except Exception as exc:  # noqa: BLE001 -- surfaced with its own reason
        raise RegistryError("language_config_invalid", f"could not load the language config: {exc}", code=2)

    try:
        senses_result = mods["canon_senses"].load_senses(
            durable_root / "canon_senses.json", allow_absent=True
        )
    except Exception as exc:  # noqa: BLE001
        raise RegistryError("canon_senses_invalid", f"canon_senses.json failed to load: {exc}", code=2)

    # `load_senses` validates STRUCTURE only. The stored evidence -- which is
    # the one authenticated place in the book this pass has for a senses-only
    # person -- is verified by the plugin's real verifier, against the
    # manifest just read. Without this call the locator is an assertion about
    # bytes that may have moved since the earlier mandatory audit.
    failures = mods["evidence_verify"].verify_senses(senses_result, manifest, language_config, canon)
    if failures:
        detail = "; ".join(str(f) for f in failures[:5])
        raise RegistryError(
            "senses_evidence_unverified",
            f"{len(failures)} canon_senses.json evidence record(s) no longer verify against "
            f"manifest.json: {detail}",
            code=2,
        )

    aggregate = mods["occurrence_targets"].build(manifest, canon, senses_result, language_config, nodestream)
    eligible = aggregate["eligible_by_source_form"]
    unresolved = aggregate["unresolved_homonyms"]

    entries = (canon or {}).get("entries") or {}
    senses_by_form = senses_result.entries_by_source_form or {}

    # -----------------------------------------------------------------
    # The prep universe. Canon alone is NOT the cast: an adjudicated
    # homonym split is deliberately absent from canon.json's entries{} --
    # that is the whole point of the sidecar (glossary_batch_plan.py's
    # split-form exclusion says so outright) -- so a canon-only universe
    # would omit exactly the people a genealogy registry is for.
    # -----------------------------------------------------------------
    units = []
    excluded = []

    for source_form, entry in sorted(entries.items()):
        if not isinstance(entry, dict):
            continue
        if not mods["occurrence_targets"].entry_is_index_eligible(entry):
            excluded.append(
                {
                    "source_form": source_form,
                    # Carried so B4 can CONSUME this target even though no unit
                    # owns it. The renderer's alternation is built from every
                    # canon entry and never branches on `is_proper_name`, so a
                    # declared-realia target it links is a span a shorter
                    # person-surface must not absorb.
                    "canonical_target_form": entry.get("canonical_target_form"),
                    "reason": "the canon entry declares itself not identity-bearing "
                    "(is_proper_name:false or basis:not_a_name)",
                }
            )
            continue
        if source_form in senses_by_form:
            # Suppressed in favour of this form's per-sense units, below --
            # narrowly, so the (source_form, sense_id) key never collides.
            continue
        records = eligible.get(source_form) or []
        homonym = unresolved.get(source_form)
        contexts, total, truncated = build_contexts(
            source_form, records, manifest, language_config, mods, args.max_contexts_per_form,
            args.context_chars, target_index, entry.get("canonical_target_form"),
        )
        units.append(
            {
                "unit": {"source_form": source_form, "sense_id": None},
                "origin_population": "canon_entry",
                "canonical_target_form": entry.get("canonical_target_form"),
                "basis": entry.get("basis"),
                "canon_confidence": entry.get("confidence"),
                "category": entry.get("category"),
                "note": entry.get("note"),
                "disambiguator": None,
                "occurrences": None if homonym else len(records),
                "occurrences_reason": (
                    f"the occurrence engine refuses to attribute this form: {homonym.get('reason')}"
                    if homonym
                    else None
                ),
                "attributable": homonym is None,
                "mentions": [
                    {
                        "seg": r.get("seg"),
                        "origin": r.get("origin"),
                        "source_block": r.get("source_block"),
                        **({"vid": r["vid"]} if r.get("vid") is not None else {}),
                        **({"footnote_n": r["footnote_n"]} if r.get("footnote_n") is not None else {}),
                    }
                    for r in records
                ],
                "contexts": contexts,
                "contexts_total": total,
                "contexts_truncated": truncated,
                "refusal_only": False,
            }
        )

    for source_form in sorted(senses_by_form):
        record = senses_by_form[source_form] or {}
        homonym = unresolved.get(source_form)
        for sense in record.get("senses") or []:
            locator = None
            context_text = None
            evidence = sense.get("evidence") or {}
            target_text, target_centred = None, False
            if evidence.get("block"):
                locator = {"origin": "block", "block": evidence["block"]}
                try:
                    text, _ = resolve_container(manifest, locator)
                    context_text = text[evidence.get("context_start", 0):evidence.get("context_end", 0)]
                except RegistryError:
                    context_text = None
                # A split form is absent from canon.entries{} and so has no
                # canonical target form; the window starts at the container.
                target_text, target_centred = target_window(
                    resolve_target_container(target_index, locator), None, args.context_chars
                )
            units.append(
                {
                    "unit": {"source_form": source_form, "sense_id": sense.get("sense_id")},
                    "origin_population": "canon_senses",
                    "canonical_target_form": None,
                    "basis": None,
                    "canon_confidence": None,
                    "category": None,
                    "note": None,
                    "disambiguator": sense.get("disambiguator"),
                    "index_scope": sense.get("index_scope"),
                    "occurrences": None,
                    "occurrences_reason": (
                        "an adjudicated homonym split records one verified occurrence per sense, not an "
                        "assignment of every occurrence, so per-sense attribution is not derivable"
                        + (f"; the occurrence engine also reports: {homonym.get('reason')}" if homonym else "")
                    ),
                    "attributable": False,
                    "mentions": [],
                    "contexts": (
                        [{"seg": evidence.get("seg"), "locator": locator, "text": context_text,
                          "window_centred_on_match": True, "target_text": target_text,
                          "target_window_centred_on_canonical_form": target_centred}]
                        if context_text
                        else []
                    ),
                    "contexts_total": 1 if context_text else 0,
                    "contexts_truncated": False,
                    "refusal_only": False,
                }
            )

    # review_queue rows, coalesced by raw source_form. The schema does not
    # require uniqueness there and the merge path appends a row whenever the
    # whole object differs, so two batches can queue one form for two
    # different reasons; collapsing them keeps BOTH notes.
    queued = {}
    for row in (canon or {}).get("review_queue") or []:
        if not isinstance(row, dict):
            continue
        form = row.get("source_form")
        if not form:
            continue
        queued.setdefault(form, []).append(row.get("note"))
    for source_form in sorted(queued):
        notes = [n for n in queued[source_form] if n]
        units.append(
            {
                "unit": {"source_form": source_form, "sense_id": None},
                "origin_population": "canon_review_queue",
                "canonical_target_form": None,
                "basis": None,
                "canon_confidence": None,
                "category": None,
                "note": " | ".join(notes),
                "disambiguator": None,
                "occurrences": None,
                "occurrences_reason": "not in canon.entries{}; the occurrence engine indexes canon "
                "entries only, so this candidate has no occurrence records",
                "attributable": False,
                "mentions": [],
                "contexts": [],
                "contexts_total": 0,
                "contexts_truncated": False,
                # The project itself records this form as unresolved. It may
                # be described, never resolved into a person by this pass.
                "refusal_only": True,
            }
        )

    body = {
        "schema_version": 1,
        # The assembled book, bound into this document's own digest so every
        # later step inherits the binding for free: `--claims` reads a printed
        # surface's evidence out of it and `--build` counts against it.
        "nodestream_sha256": sha256_hex(nodestream),
        # The SOURCE, bound the same way and for the same reason: the units
        # below quote it, their locators point into it, and the senses evidence
        # was verified against it -- once, here.
        "manifest_sha256": sha256_hex(manifest),
        "units": units,
        "excluded_by_canon_declaration": excluded,
        "counts": {
            "units": len(units),
            "canon_entry": sum(1 for u in units if u["origin_population"] == "canon_entry"),
            "canon_senses": sum(1 for u in units if u["origin_population"] == "canon_senses"),
            "canon_review_queue": sum(1 for u in units if u["origin_population"] == "canon_review_queue"),
            "refusal_only": sum(1 for u in units if u["refusal_only"]),
            "contexts_truncated": sum(1 for u in units if u["contexts_truncated"]),
        },
    }
    digest = sha256_hex(body)
    doc = dict(body)
    doc["input_sha256"] = digest

    text = emitted_json_text(doc)
    size = len(text.encode("utf-8"))
    if size > args.max_input_chars:
        raise RegistryError(
            "input_too_large",
            f"registry_input.json would be {size} bytes, over --max-input-chars {args.max_input_chars}; "
            f"lower --max-contexts-per-form/--context-chars or raise the cap deliberately -- this pass "
            f"never truncates its own input silently",
            code=2,
        )

    write_text(durable_root / "registry" / "registry_input.json", text)
    return {
        "success": True,
        "mode": "prep",
        "input_sha256": digest,
        "bytes": size,
        **body["counts"],
    }


# ---------------------------------------------------------------------------
# Pre-claims gates P1-P5. Run by --claims before it emits, and re-run by
# --build, which never assumes --claims ran over the same bytes.
# ---------------------------------------------------------------------------

def verify_nodestream(prep: dict, nodestream: dict) -> str:
    """The assembled book is an INPUT to this chain, so it is bound like one.

    `--claims` reads every `printed_surface` claim's evidence out of the
    delivered corpus, and `--build` counts against it. Neither the prep digest
    nor the verdict digest nor the claims digest covers those bytes, so a
    NodeStream re-assembled or hand-edited between the steps would have Pass B's
    affirmations -- given for passages it was shown -- applied to a different
    book. The dangerous shape is not the obvious one (a surface that vanishes
    reports `not_found_in_target_text`, which is loud); it is a surface that
    still occurs, somewhere else, belonging to someone else, and is counted
    silently.
    """
    stated = prep.get("nodestream_sha256")
    actual = sha256_hex(nodestream)
    if stated != actual:
        raise RegistryError(
            "nodestream_changed",
            f"the assembled NodeStream has changed since --prep ran ({stated} -> {actual}); "
            f"every judgement in this chain was made against the old one -- re-run --prep, "
            f"Pass A, --claims and Pass B against the current book",
        )
    return actual


def verify_manifest(prep: dict, manifest: dict) -> str:
    """The source is an INPUT to this chain too, and it is bound like one.

    `--prep` snapshots its quotes and locators into the units, and runs the
    plugin's real evidence verifier over `canon_senses.json` against it -- once.
    A senses-only person's one authenticated place in the book is that record,
    and nothing re-checks it later. P5 is not a substitute: it re-reads only the
    quotes Pass A chose to cite, so a source edited between the steps can keep
    every cited quote intact while the evidence a person's IDENTITY rests on has
    gone. Binding the manifest closes that: an edit anywhere in the source moves
    this digest and the chain refuses rather than adjudicating a book that is no
    longer the one it read.
    """
    stated = prep.get("manifest_sha256")
    actual = sha256_hex(manifest)
    if stated != actual:
        raise RegistryError(
            "manifest_changed",
            f"manifest.json has changed since --prep ran ({stated} -> {actual}); "
            f"the units, their locators and the senses evidence were all taken from the old "
            f"source -- re-run --prep, Pass A, --claims and Pass B against the current one",
        )
    return actual


def load_prep(durable_root: Path):
    doc = read_json(durable_root / "registry" / "registry_input.json", "registry/registry_input.json")
    stated = doc.get("input_sha256")
    body = {k: v for k, v in doc.items() if k != "input_sha256"}
    recomputed = sha256_hex(body)
    if stated != recomputed:
        raise RegistryError(
            "prep_self_digest_mismatch",
            f"registry_input.json's own input_sha256 {stated} does not match its body ({recomputed}); "
            f"it has been edited by hand -- re-run --prep",
        )
    return doc, recomputed


def run_pre_claims_gates(prep: dict, prep_digest: str, verdicts: dict, manifest: dict) -> dict:
    """P1 is the caller's (schema validation). P2-P5 here.

    Returns the resolved index the claims projection and the build both need,
    so neither re-derives it from the raw documents twice.
    """
    # --- P2 freshness -------------------------------------------------
    if verdicts.get("input_sha256") != prep_digest:
        raise RegistryError(
            "verdicts_stale",
            f"registry_verdicts.json was produced against input_sha256 "
            f"{verdicts.get('input_sha256')}, but registry_input.json on disk is {prep_digest}; "
            f"a verdict over a different canon is refused, never consumed",
        )

    by_key = {}
    refusal_only = set()
    for unit in prep.get("units") or []:
        key = unit_key(unit["unit"]["source_form"], unit["unit"]["sense_id"])
        by_key[key] = unit
        if unit.get("refusal_only"):
            refusal_only.add(key)

    # --- P3 coverage, keyed on (source_form, sense_id) ------------------
    seen = {}
    for person in verdicts.get("people") or []:
        for u in person.get("units") or []:
            seen.setdefault(unit_key(u["source_form"], u["sense_id"]), []).append(("people", person["person_id"]))
    for row in verdicts.get("non_person_forms") or []:
        u = row["unit"]
        seen.setdefault(unit_key(u["source_form"], u["sense_id"]), []).append(("non_person_forms", None))
    for row in verdicts.get("refusals") or []:
        u = row["unit"]
        seen.setdefault(unit_key(u["source_form"], u["sense_id"]), []).append(("refusals", None))

    # --- P4 no invented units (checked before coverage is reported, so an
    # invented unit is never mistaken for a duplicate) -------------------
    # `sense_id` is a string or None, so a bare sort over the tuples compares
    # str with None and raises. The tag keeps a total order without inventing
    # one between the two kinds.
    def unit_sort_key(k):
        return (k[0], k[1] is not None, k[1] or "")

    invented = sorted((k for k in seen if k not in by_key), key=unit_sort_key)
    if invented:
        raise RegistryError(
            "invented_units",
            "the verdict claims unit(s) the prep input does not contain: "
            + ", ".join(f"{f!r}/{s!r}" for f, s in invented[:10]),
        )

    missing = sorted((k for k in by_key if k not in seen), key=unit_sort_key)
    duplicated = sorted((k for k, v in seen.items() if len(v) > 1), key=unit_sort_key)
    if missing or duplicated:
        parts = []
        if missing:
            parts.append("never claimed: " + ", ".join(f"{f!r}/{s!r}" for f, s in missing[:10]))
        if duplicated:
            parts.append("claimed more than once: " + ", ".join(f"{f!r}/{s!r}" for f, s in duplicated[:10]))
        raise RegistryError(
            "coverage_violation",
            "every prep unit must appear exactly once across people[].units, non_person_forms[] and "
            "refusals[] -- " + "; ".join(parts),
        )

    misplaced = sorted(k for k in refusal_only if seen[k][0][0] != "refusals")
    if misplaced:
        raise RegistryError(
            "refusal_only_misplaced",
            "the project's own canon review_queue records these form(s) as unresolved, so they may only "
            "appear in refusals[]: " + ", ".join(f"{f!r}" for f, _ in misplaced[:10]),
        )

    # --- P5 evidence exists, at the right locator -----------------------
    for person in verdicts.get("people") or []:
        for surface in person.get("printed_surfaces") or []:
            if SENTINEL_RE.search(surface):
                raise RegistryError(
                    "surface_contains_sentinel",
                    f"person {person['person_id']!r} proposes printed surface {surface!r}, which contains "
                    f"an assembly sentinel bracket -- a sentinel is replaced by the renderer and is never "
                    f"a printed name",
                )
        for kind, rows in (("relation", person.get("relations")), ("place", person.get("places")),
                           ("date", person.get("dates"))):
            for row in rows or []:
                ev = row["evidence"]
                text, where = resolve_container(manifest, ev["locator"])
                if ev["quote"] not in text:
                    raise RegistryError(
                        "quote_not_in_container",
                        f"person {person['person_id']!r} cites a {kind} quote that does not appear "
                        f"verbatim in {where}: {ev['quote'][:80]!r}",
                    )

    # --- P7 no line breaks in the fields that become a Markdown line ------
    # PEOPLE.md is the artifact a genealogy reader actually reads, and it is
    # built by interpolating these strings into headings and bullets. A
    # display_name carrying "\n\n## Refused" writes a whole section that no
    # adjudication produced; an identity_note carrying "\n- **son_of** X"
    # writes a kinship edge indistinguishable from an affirmed one. No
    # adversary is needed -- a model emitting a two-line note does it by
    # accident. Refused here, where the operator sees which field it was,
    # rather than only neutralised at render time (render_people_md collapses
    # them as well, so neither layer is the only thing standing between a
    # stray newline and a forged claim). Evidence QUOTES are deliberately not
    # included: a verse spans lines, and its quote must stay verbatim to be
    # verifiable -- the renderer collapses those instead.
    for person in verdicts.get("people") or []:
        pid = person.get("person_id")
        checked = [("display_name", person.get("display_name")),
                   ("identity_note", person.get("identity_note")),
                   ("identity_status_reason", person.get("identity_status_reason"))]
        checked += [("printed_surfaces[]", v) for v in person.get("printed_surfaces") or []]
        for rel in person.get("relations") or []:
            checked.append(("relations[].to_unregistered", rel.get("to_unregistered")))
        for place in person.get("places") or []:
            checked.append(("places[].name", place.get("name")))
        for date in person.get("dates") or []:
            checked.append(("dates[].value", date.get("value")))
        for field, value in checked:
            if isinstance(value, str) and ("\n" in value or "\r" in value):
                raise RegistryError(
                    "line_break_in_field",
                    f"person {pid!r} has a line break in {field}; these strings become a single "
                    f"line of PEOPLE.md, where a break writes a heading or a bullet the "
                    f"adjudication never produced: {value[:60]!r}",
                )
    for row in verdicts.get("non_person_forms") or []:
        if isinstance(row.get("reason"), str) and ("\n" in row["reason"] or "\r" in row["reason"]):
            raise RegistryError("line_break_in_field",
                                f"non_person_forms reason for {row['unit']['source_form']!r} "
                                f"contains a line break")
    for row in verdicts.get("refusals") or []:
        if isinstance(row.get("reason"), str) and ("\n" in row["reason"] or "\r" in row["reason"]):
            raise RegistryError("line_break_in_field",
                                f"refusals reason for {row['unit']['source_form']!r} "
                                f"contains a line break")

    # --- P6 referential integrity WITHIN the verdict. B5 re-checks it after
    # adjudication, against the cast that actually survived ----------------
    ids = [p["person_id"] for p in verdicts.get("people") or []]
    known_ids = set(ids)
    if len(known_ids) != len(ids):
        raise RegistryError("duplicate_person_id", "person_id values must be unique within the verdict")
    for person in verdicts.get("people") or []:
        for rel in person.get("relations") or []:
            target = rel.get("to_person_id")
            if target is not None and target not in known_ids:
                raise RegistryError(
                    "dangling_relation_target",
                    f"person {person['person_id']!r} names to_person_id {target!r}, which no person has",
                )

    return {"by_key": by_key, "refusal_only": refusal_only}


# ---------------------------------------------------------------------------
# --claims
# ---------------------------------------------------------------------------

def unit_evidence_payload(prep_unit: dict) -> dict:
    """What an adjudicator needs about one unit, and nothing more. No person
    narrative, no other claims, no Pass A framing."""
    return {
        "unit": prep_unit["unit"],
        "canonical_target_form": prep_unit.get("canonical_target_form"),
        "disambiguator": prep_unit.get("disambiguator"),
        "note": prep_unit.get("note"),
        "contexts": prep_unit.get("contexts") or [],
        "contexts_total": prep_unit.get("contexts_total"),
        "contexts_truncated": prep_unit.get("contexts_truncated"),
    }


def surface_windows(corpus: str, surface: str, cap: int, context_chars: int) -> tuple:
    """Where the delivered book actually shows this string, windowed.

    Plain substring spans, deliberately: the question a `printed_surface` claim
    asks is "does the book print this as a name for this person", and a
    boundary-refused occurrence is still something the adjudicator must be able
    to see and rule on. The COUNT is a separate computation with the renderer's
    own rule; this is evidence, not arithmetic."""
    surface = nfc(surface or "")
    if not surface:
        return [], 0, False
    spans = [m.span() for m in re.finditer(re.escape(surface), corpus)]
    half = max(context_chars // 2, 1)
    kept = spread(spans, cap)
    windows, seen = [], set()
    for a, b in kept:
        window = corpus[max(0, a - half):min(len(corpus), b + half)]
        # Occurrences close together share a window verbatim. Repeating it once
        # per occurrence would spend the adjudicator's attention on the same
        # sentence and read as more evidence than it is; the true occurrence
        # count travels beside the list, where it is a number rather than an
        # impression.
        if window in seen:
            continue
        seen.add(window)
        windows.append(window)
    # Truncation is DISCLOSED rather than removed, the same accepted tradeoff
    # the source contexts make: a principal figure printed hundreds of times
    # would otherwise put hundreds of windows in one claim. The adjudicator is
    # told the true total and that it is seeing a spread, so it judges on what
    # it has instead of on what it assumes it has.
    return windows, len(spans), len(kept) < len(spans)


def project_claims(verdicts: dict, index: dict, manifest: dict,
                   corpus: str = "", max_windows: int = 8, context_chars: int = 400) -> list:
    """One entry per judgement, each independently judgeable.

    A PERSON claim asks two things at once -- do these units denote a person
    at all, and (when there is more than one) do they denote the SAME person.
    One claim rather than two, because both are the same judgement from the
    same evidence, and because every emitted person must pass through exactly
    one adjudication: a single-unit person is an assertion too. `category` is
    optional on a canon entry and absent on most, so "this is a person and not
    a place" is a model's judgement, never a fact read off disk.
    """
    by_key = index["by_key"]
    claims = []

    people_by_id = {p["person_id"]: p for p in verdicts.get("people") or []}

    def identity_card(person: dict) -> dict:
        """Who a person is, in the terms the adjudicator can check: the name,
        and the units (with their target forms) that carry it. A relation claim
        that named only an opaque `to_person_id` would ask Pass B to confirm a
        claim "about these exact parties" while hiding one of the parties."""
        return {
            "display_name": person["display_name"],
            "units": [
                {
                    "unit": u,
                    "canonical_target_form":
                        by_key[unit_key(u["source_form"], u["sense_id"])].get("canonical_target_form"),
                    "disambiguator":
                        by_key[unit_key(u["source_form"], u["sense_id"])].get("disambiguator"),
                }
                for u in person["units"]
            ],
        }

    def add(kind, person_id, payload, question):
        body = {"kind": kind, "person_id": person_id, "question": question, "payload": payload}
        body["claim_id"] = sha256_hex({k: v for k, v in body.items()})
        claims.append(body)

    for row in verdicts.get("non_person_forms") or []:
        # Classifying a unit as a place or a work removes a candidate from the
        # cast, and a person removed from a genealogy registry is exactly as
        # silent as a person invented into one. So it is adjudicated too, and
        # an unaffirmed classification becomes a refusal rather than a fact.
        key = unit_key(row["unit"]["source_form"], row["unit"]["sense_id"])
        add(
            "non_person",
            None,
            {"unit": row["unit"], "claimed_kind": row["kind"], "claimed_reason": row["reason"],
             "evidence": unit_evidence_payload(by_key[key])},
            "This record says the form below does NOT denote a person, but the kind named, for the "
            "reason given. Do the contexts shown support BOTH the classification and that reason? "
            "The reason is copied verbatim into the registry, so an unsupported one is an unchecked "
            "assertion about the book. Refuse if the form could denote a human being.",
        )

    for person in verdicts.get("people") or []:
        units = [unit_evidence_payload(by_key[unit_key(u["source_form"], u["sense_id"])])
                 for u in person["units"]]
        add(
            "person",
            person["person_id"],
            {"display_name": person["display_name"], "identity_note": person["identity_note"],
             "units": units},
            "Four things, and all four must hold to affirm. (1) Do the source-language forms below "
            "denote a person, rather than a place, a work, a group or a common noun? (2) If there is "
            "more than one form, do they all denote the SAME person? Refuse if they are consistent "
            "with two or more distinct people. (3) Is the identity note supported by the contexts "
            "shown? It is prose that reaches the reader verbatim, so a relation or a fact asserted "
            "there is a claim like any other -- and it is not covered by the typed claims. (4) Is "
            "the display name a name this person is actually called in the book, rather than one "
            "assembled from an assumption about them? It heads their entry in the registry.",
        )
        # BOTH values are adjudicated, not only `confirmed`. `contested` is
        # the safe status, but its stated REASON is the sentence a genealogy
        # reader leans on hardest, and the failure the issue names outright is
        # a reason that talks about SCARCITY when the field is about IDENTITY.
        add(
            "identity_status",
            person["person_id"],
            {
                "display_name": person["display_name"],
                "claimed_status": person["identity_status"],
                "claimed_reason": person["identity_status_reason"],
                "units": units,
            },
            "This record states whether the person's identity is settled (`confirmed`) or not "
            "(`contested`), and gives a reason. Two questions, both must hold. (1) Is that status "
            "right on the evidence shown? (2) Is the reason about the IDENTITY -- whether this "
            "person can be told apart from a namesake -- rather than about how OFTEN they appear? "
            "A person mentioned once whose identity is obvious is confirmed; a person mentioned two "
            "hundred times who cannot be told apart from a namesake is contested. \"Only one "
            "mention\" is a statement about scarcity and never a reason for either status.",
        )
        for surface in person.get("printed_surfaces") or []:
            windows, total_windows, truncated_windows = surface_windows(
                corpus, surface, max_windows, context_chars
            )
            add(
                "printed_surface",
                person["person_id"],
                {
                    "surface": surface,
                    "units": units,
                    # The delivered text itself, so this is checkable rather
                    # than a second guess: an adjudicator shown only source
                    # contexts cannot tell a real printed inflection from an
                    # invented one, and neither could the pass that proposed it.
                    "target_occurrences": windows,
                    "target_occurrence_total": total_windows,
                    "target_occurrences_truncated": truncated_windows,
                },
                "Does the book print the target-language string below as a name for this person? "
                f"The string appears {total_windows} time(s) in the delivered text; the passages "
                "shown are "
                + ("an even spread over them, NOT all of them -- judge on what the spread shows"
                   if truncated_windows else "all of them")
                + ". An empty list means the book does not print it at all; a string that appears "
                "only inside a longer word, or only as another person's name, is not this person's "
                "printed form.",
            )
        for kind, rows in (("relation", person.get("relations")), ("place", person.get("places")),
                           ("date", person.get("dates"))):
            for row in rows or []:
                ev = row["evidence"]
                container, where = resolve_container(manifest, ev["locator"])
                claim = {k: v for k, v in row.items() if k != "evidence"}
                target = people_by_id.get(claim.get("to_person_id"))
                add(
                    kind,
                    person["person_id"],
                    {
                        "claim": claim,
                        "subject": identity_card(person),
                        **({"object": identity_card(target)} if target is not None else {}),
                        "quote": ev["quote"],
                        "locator": ev["locator"],
                        "container_description": where,
                        "container_text": container,
                    },
                    "Does the quoted sentence STATE this exact typed claim about these exact parties? A "
                    "sentence that merely mentions them together is not a statement of the claim.",
                )
    return claims


def cmd_claims(args, durable_root: Path, schema_dir: Path) -> dict:
    manifest = read_json(durable_root / "manifest.json", "manifest.json")
    nodestream = read_json(durable_root / "out" / ".assembled" / "nodestream.json",
                           "the assembled NodeStream (out/.assembled/nodestream.json)")
    prep, prep_digest = load_prep(durable_root)
    verify_nodestream(prep, nodestream)
    verify_manifest(prep, manifest)
    verdicts = read_json(durable_root / "registry" / "registry_verdicts.json",
                         "registry/registry_verdicts.json (Pass A's output)")

    validate_or_raise(load_validator(schema_dir, VERDICTS_SCHEMA), verdicts,
                      "verdicts_schema_invalid", "registry_verdicts.json")
    index = run_pre_claims_gates(prep, prep_digest, verdicts, manifest)

    claims = project_claims(verdicts, index, manifest, assembled_target_text(nodestream),
                            args.max_contexts_per_form, args.context_chars)
    # The claims body carries the digest of the VERDICT it was projected from,
    # not just of the prep. Binding to the prep alone leaves a real gap: a
    # verdict edited after --claims ran keeps a valid `input_sha256`, so the
    # build would re-derive a DIFFERENT projection (different units, different
    # claim payloads) while Pass B's affirmations -- keyed on claim_id -- were
    # given for the old one. Hashing the verdict in closes it: any edit to the
    # verdict moves this digest and the build refuses.
    verdicts_digest = sha256_hex(verdicts)
    body = {"schema_version": 1, "input_sha256": prep_digest,
            "verdicts_sha256": verdicts_digest, "claims": claims}
    claims_digest = sha256_hex(body)
    doc = dict(body)
    doc["claims_sha256"] = claims_digest

    # Pass B's ENTIRE input, capped for the same reason --prep's is. The
    # projection re-embeds a person's evidence payload into every one of that
    # person's claims, so this document is a multiple of the prep -- and the
    # multiple is not fixed: it grows with claims per person, which is exactly
    # what a densely-related cast produces. A prep well under its own cap can
    # therefore project a document no adjudicator will read whole, and a
    # silently truncated Pass B is an unchecked Pass A, which is the one
    # failure this design has no other guard against.
    text = emitted_json_text(doc)
    size = len(text.encode("utf-8"))
    if size > args.max_claims_chars:
        raise RegistryError(
            "claims_too_large",
            f"registry_claims.json would be {size} bytes, over --max-claims-chars "
            f"{args.max_claims_chars}; lower --max-contexts-per-form/--context-chars or raise the "
            f"cap deliberately -- Pass B reads this document whole or it is not the check it is "
            f"relied on to be",
            code=2,
        )
    write_text(durable_root / "registry" / "registry_claims.json", text)

    kinds = {}
    for c in claims:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    return {
        "success": True,
        "mode": "claims",
        "input_sha256": prep_digest,
        "verdicts_sha256": verdicts_digest,
        "claims_sha256": claims_digest,
        "claims": len(claims),
        "by_kind": kinds,
    }


# ---------------------------------------------------------------------------
# --build
# ---------------------------------------------------------------------------

def assembled_target_text(nodestream: dict) -> str:
    """The corpus printed-name counting runs over: every node's text, each of
    its verses' rendered and literal_gloss, and every footnote's text, in
    NodeStream order. Both a rendered verse and its gloss are printed in the
    delivered vault when the policy emits both, so a name appearing in each is
    printed twice and counted twice."""
    parts = []
    for node in (nodestream or {}).get("nodes") or []:
        if not isinstance(node, dict):
            continue
        # A verse placeholder becomes a HARD SEAM (a newline), never a space
        # and never the verse's own text spliced in. The renderer resolves the
        # placeholder to the verse wrapped in its own markup, so no printed name
        # spans that join; splicing the verse in bare would let `John⟦…⟧Smith`
        # count a `John Smith` the book never prints, and leaving the raw token
        # would leave a string printed nowhere in a corpus that counts printed
        # strings. The verse's delivered halves are appended below, so nothing
        # is lost -- every part of this corpus is joined by the same hard seam,
        # and no surface can be matched across one.
        parts.append(PLACEHOLDER_RE.sub("\n", node.get("text") or ""))
        for claim in node.get("verses") or []:
            content = (claim or {}).get("content") or {}
            parts.append(content.get("rendered") or "")
            parts.append(content.get("literal_gloss") or "")
    delivered = delivered_footnote_ns(nodestream)
    for fn in (nodestream or {}).get("footnotes") or []:
        if isinstance(fn, dict) and fn.get("n") in delivered:
            parts.append(fn.get("text") or "")
    return nfc("\n".join(parts))


def boundary_ok(text: str, start: int, end: int) -> bool:
    """The plugin's own word-boundary rule, applied to counting.

    Byte-identical in behaviour to `render_obsidian._Linker.link`'s
    `_boundary_ok` (#587) -- a match is refused when the character immediately
    before or after it is `str.isalnum()`. Duplicated rather than imported
    because that predicate is a closure inside the linker's own scan, and this
    project's convention is a byte-identical duplicate over a shared lib
    between self-contained scripts (see `draft_sha1.py` / `ledger_update.py`).

    Keeping the SAME rule is the point, not an aesthetic: if the registry
    counted a printed name under one boundary rule while the vault linked it
    under another, the two would disagree about the same book, and the
    disagreement would look like a data problem rather than two implementations
    of one decision.

    Deliberately `isalnum()` on the ADJACENT CHARACTER, never `\\b` or `\\w`:
    `\\b` is defined against the PATTERN's own edge characters, so for a target
    beginning or ending with punctuation ("R.") it is wrong in both directions.
    Alphanumeric rather than non-space, so a following apostrophe, comma or
    period still counts. Combining marks are not `isalnum()`, so a word
    continued by one is still treated as ended -- the same known gap the linker
    carries (#590)."""
    return not (
        (start > 0 and text[start - 1].isalnum())
        or (end < len(text) and text[end].isalnum())
    )


def count_surfaces(surfaces: list, consume_also: list, corpus: str, boundary: str) -> dict:
    """Count each surface in the assembled target text, longest first, with
    matched spans consumed.

    `consume_also` carries every OTHER known target surface in the project --
    including those belonging to refused and non-person units, which get no
    owner. Without them, a short surface absorbs occurrences of a longer form
    that simply has nobody to attribute it to (`Ben` inside an unowned
    `Ben-Gurion`); longest-first consumption only protects a span that is in
    the inventory at all.

    Boundary handling: `word` applies `boundary_ok` per match, so `Teplik` is
    not counted inside `Tepliker`. That rule cannot work for a target language
    that does not space its words -- in a no-space script the particle after a
    name is `isalnum()` too. So a surface that is printed in the book but has
    no countable span is reported `boundary_ambiguous`, with the substring
    count and NO count. Two things produce that signature -- the boundary rule
    refused every match, or a longer inventory form consumed them -- and it is
    not an error and does not abort the build: `Ann` inside `Joanna` looks
    identical to a no-space script, and nothing in the text distinguishes them.
    `--surface-boundary none` counts plain substrings instead, for a no-space
    target, at that cost.
    """
    # ONE combined alternation, sorted longest-first, scanned once -- the
    # renderer's own construction (`render_obsidian.py`'s `_Linker`), not an
    # imitation of its outcome. Per-surface scanning with masking between
    # passes is NOT equivalent: it resolves an overlap in favour of the LONGER
    # surface whenever the LONGER one starts later, while one leftmost-first
    # scan resolves it in favour of the EARLIER one. Over "R. Nachman of
    # Tulchin" the renderer links `R. Nachman`; a longest-first sweep would
    # instead consume `Nachman of Tulchin` and report `R. Nachman` as printed
    # nowhere. Longest-first still decides ties AT one offset, which is the
    # guarantee that rule actually makes.
    #
    # The lexicographic tiebreak is not cosmetic: a bare `key=len` over a set
    # leaves equal-length surfaces in hash order, which varies per process, and
    # this artifact is supposed to be byte-identical across re-runs.
    inventory = sorted({nfc(s) for s in list(surfaces) + list(consume_also) if s},
                       key=lambda t: (-len(t), t))
    wanted = {nfc(s) for s in surfaces if s}
    counts = {surface: 0 for surface in wanted}
    if inventory:
        pattern = re.compile("|".join(re.escape(surface) for surface in inventory))
        for m in pattern.finditer(corpus):
            # `finditer` is non-overlapping, so a boundary-refused span is
            # still consumed and a shorter surface starting inside it gets no
            # turn -- deliberate, and the renderer's own documented choice.
            if boundary != "none" and not boundary_ok(corpus, m.start(), m.end()):
                continue
            hit = m.group(0)
            if hit in counts:
                counts[hit] += 1

    results = {}
    for surface in sorted(wanted):
        if counts[surface]:
            results[surface] = {"count": counts[surface], "status": "counted", "substring_count": None}
            continue
        # The substring probe reads the DELIVERED corpus, never a residue:
        # `not_found_in_target_text` is a claim about the book, and a surface
        # swallowed by a longer form ("Marie" inside "JoAnn Marie") is printed
        # there. Reporting it absent would be a false statement in the artifact,
        # in the misleading direction -- a reader concludes the book never
        # prints that name.
        substrings = len(re.findall(re.escape(surface), corpus))
        results[surface] = (
            {"count": None, "status": "boundary_ambiguous", "substring_count": substrings}
            if substrings
            else {"count": None, "status": "not_found_in_target_text", "substring_count": 0}
        )
    return results


def oneline(value) -> str:
    """One Markdown line's worth of text, whatever the input contained.

    Every value interpolated below passes through this. P7 already refuses a
    line break in the identity fields, but an evidence QUOTE may legitimately
    span lines -- a verse does -- and it must stay verbatim in the JSON to
    remain verifiable. So the JSON keeps the break and the Markdown does not:
    a bullet cannot be split into a forged second bullet by the contents of a
    quote. Belt and braces on purpose, since a single layer here is one edit
    away from being the only thing between a stray newline and a claim the
    registry never made."""
    return " ".join(str(value if value is not None else "").split())


def render_people_md(registry: dict) -> str:
    """A deterministic human rendering. No timestamp, so a re-run over
    unchanged inputs produces byte-identical output."""
    lines = ["# People", ""]
    s = registry["summary"]
    # A relation points at a person_id, which is an identifier and not a name.
    # PEOPLE.md is the file a genealogy reader actually reads, so it resolves
    # the edge to the person it names; the JSON keeps the id.
    name_by_id = {p["person_id"]: oneline(p["display_name"]) for p in registry["people"]}
    lines.append(
        f"{s['people']} people · {s['refusals']} refused · {s['non_person_forms']} not people · "
        f"{s['refuted_claims']} claims not carried"
    )
    lines.append("")
    lines.append(
        "Occurrence counts are computed by the plugin, never asserted by a model. "
        "`assembly_currency: not_bound` — see the registry JSON for what that means."
    )
    lines.append("")
    for person in registry["people"]:
        lines.append(f"## {oneline(person['display_name'])}")
        lines.append("")
        lines.append(f"- **Identity** — {oneline(person['identity_note'])}")
        lines.append(
            f"- **Status** — {person['identity_status']}: {oneline(person['identity_status_reason'])}"
            + ("  _(judged on truncated evidence)_" if person["evidence_truncated"] else "")
        )
        forms = ", ".join(
            f"`{oneline(u['source_form'])}`" + (f" [{oneline(u['sense_id'])}]" if u["sense_id"] else "")
            for u in person["units"]
        )
        lines.append(f"- **Source forms** — {forms}")
        if person["printed_forms"]:
            printed = ", ".join(
                f"{oneline(p['surface'])} ({p['count']}×)" if p["status"] == "counted"
                else f"{oneline(p['surface'])} ({p['status']})"
                for p in person["printed_forms"]
            )
            lines.append(f"- **Printed as** — {printed}")
        if person["mention_count"] is None:
            lines.append(f"- **Mentions** — not attributable: {oneline(person.get('mention_count_reason', ''))}")
        else:
            segs = sorted({m["seg"] for m in person["mentions"] if m.get("seg")})
            lines.append(f"- **Mentions** — {person['mention_count']} in {', '.join(segs) or 'no segment'}")
        for rel in person["relations"]:
            if rel.get("to_person_id"):
                target = name_by_id.get(rel["to_person_id"], rel["to_person_id"])
            else:
                target = f"{oneline(rel.get('to_unregistered'))} _(not in this book's cast)_"
            lines.append(f"- **{rel['type']}** {oneline(target)} — “{oneline(rel['quote'])}”")
        for place in person["places"]:
            lines.append(f"- **{place['role']}** {oneline(place['name'])} — “{oneline(place['quote'])}”")
        for date in person["dates"]:
            lines.append(f"- **{date['kind']}** {oneline(date['value'])} — “{oneline(date['quote'])}”")
        lines.append("")
    if registry["shared_printed_forms"]:
        lines.append("## Printed forms shared by several people")
        lines.append("")
        lines.append("Borne by more than one person, so attributed to nobody and counted for nobody.")
        lines.append("")
        for row in registry["shared_printed_forms"]:
            borne_by = ", ".join(oneline(name_by_id.get(c, c)) for c in row["candidates"])
            lines.append(f"- `{oneline(row['surface'])}` — {borne_by}")
        lines.append("")
    if registry["refusals"]:
        lines.append("## Refused")
        lines.append("")
        for row in registry["refusals"]:
            form = row["unit"]["source_form"]
            sense = f" [{oneline(row['unit']['sense_id'])}]" if row["unit"]["sense_id"] else ""
            lines.append(f"- `{oneline(form)}`{sense} — {oneline(row['reason'])} ({row['refused_by']})")
        lines.append("")
    return "\n".join(lines) + "\n"


def cmd_build(args, durable_root: Path, schema_dir: Path) -> dict:
    manifest = read_json(durable_root / "manifest.json", "manifest.json")
    nodestream = read_json(durable_root / "out" / ".assembled" / "nodestream.json",
                           "the assembled NodeStream (out/.assembled/nodestream.json)")
    prep, prep_digest = load_prep(durable_root)
    nodestream_digest = verify_nodestream(prep, nodestream)
    manifest_digest = verify_manifest(prep, manifest)
    verdicts = read_json(durable_root / "registry" / "registry_verdicts.json",
                         "registry/registry_verdicts.json (Pass A's output)")
    claims_doc = read_json(durable_root / "registry" / "registry_claims.json",
                           "registry/registry_claims.json -- run --claims first")
    adjudications = read_json(durable_root / "registry" / "registry_adjudications.json",
                              "registry/registry_adjudications.json (Pass B's output)")

    # P1-P5, re-run rather than assumed: --claims may have run over other bytes.
    validate_or_raise(load_validator(schema_dir, VERDICTS_SCHEMA), verdicts,
                      "verdicts_schema_invalid", "registry_verdicts.json")
    index = run_pre_claims_gates(prep, prep_digest, verdicts, manifest)

    # B1 adjudication schema + digests.
    validate_or_raise(load_validator(schema_dir, ADJUDICATIONS_SCHEMA), adjudications,
                      "adjudications_schema_invalid", "registry_adjudications.json")
    claims_body = {k: v for k, v in claims_doc.items() if k != "claims_sha256"}
    claims_digest = sha256_hex(claims_body)
    if claims_doc.get("claims_sha256") != claims_digest:
        raise RegistryError("claims_self_digest_mismatch",
                            "registry_claims.json's own claims_sha256 does not match its body -- re-run --claims")
    if claims_doc.get("input_sha256") != prep_digest:
        raise RegistryError("claims_stale",
                            "registry_claims.json was projected from a different registry_input.json -- re-run --claims")
    verdicts_digest = sha256_hex(verdicts)
    if claims_doc.get("verdicts_sha256") != verdicts_digest:
        raise RegistryError(
            "claims_stale",
            f"registry_claims.json was projected from registry_verdicts.json "
            f"{claims_doc.get('verdicts_sha256')}, but the verdict on disk is {verdicts_digest}; "
            f"applying Pass B's affirmations to a re-projected claim set would attach a judgement "
            f"to material it was never shown -- re-run --claims and Pass B",
        )
    if adjudications.get("claims_sha256") != claims_digest:
        raise RegistryError(
            "adjudications_stale",
            f"registry_adjudications.json judges claims_sha256 {adjudications.get('claims_sha256')}, but "
            f"registry_claims.json on disk is {claims_digest}",
        )
    if adjudications.get("input_sha256") != prep_digest:
        raise RegistryError("adjudications_stale",
                            "registry_adjudications.json is bound to a different registry_input.json")

    # B2 adjudication join -- exact one-to-one on claim_id. A positional list
    # of anonymous booleans could apply an affirmation meant for a safe claim
    # to an unsafe one, and nothing downstream would see it.
    claims = claims_doc.get("claims") or []
    claim_by_id = {c["claim_id"]: c for c in claims}
    verdict_by_id = {}
    for row in adjudications.get("adjudications") or []:
        if row["claim_id"] in verdict_by_id:
            raise RegistryError("adjudication_duplicate",
                                f"claim_id {row['claim_id']} is adjudicated more than once")
        verdict_by_id[row["claim_id"]] = row
    invented = sorted(set(verdict_by_id) - set(claim_by_id))
    missing = sorted(set(claim_by_id) - set(verdict_by_id))
    if invented:
        raise RegistryError("adjudication_invented",
                            f"{len(invented)} adjudication(s) carry a claim_id the claims document does not "
                            f"contain, first {invented[0]}")
    if missing:
        raise RegistryError("adjudication_missing",
                            f"{len(missing)} claim(s) were not adjudicated, first {missing[0]}")

    def affirmed(claim_id) -> bool:
        row = verdict_by_id.get(claim_id)
        return bool(row and row.get("affirmed") is True)

    # B3 apply.
    by_key = index["by_key"]
    refuted_claims = []
    refusals = []
    people_out = []

    def refute(claim, reason):
        payload = claim.get("payload") or {}
        summary = (
            payload.get("surface")
            or payload.get("display_name")
            or json.dumps(payload.get("claim") or {}, ensure_ascii=False)
        )
        refuted_claims.append(
            {
                "claim_id": claim["claim_id"],
                "kind": claim["kind"],
                "person_id": claim["person_id"],
                "summary": str(summary)[:200] or "(no summary)",
                "reason": reason,
            }
        )

    claims_by_person = {}
    non_person_claim_by_unit = {}
    for claim in claims:
        claims_by_person.setdefault(claim["person_id"], []).append(claim)
        if claim["kind"] == "non_person":
            unit = claim["payload"]["unit"]
            non_person_claim_by_unit[unit_key(unit["source_form"], unit["sense_id"])] = claim

    # A non-person classification is applied only when it was affirmed. An
    # unaffirmed one does not silently stand: the unit moves to refusals[],
    # where an operator sees it, rather than disappearing out of the cast.
    non_person_out = []
    for row in verdicts.get("non_person_forms") or []:
        key = unit_key(row["unit"]["source_form"], row["unit"]["sense_id"])
        claim = non_person_claim_by_unit.get(key)
        if claim is not None and affirmed(claim["claim_id"]):
            non_person_out.append({"unit": row["unit"], "kind": row["kind"], "reason": row["reason"]})
            continue
        reason = (verdict_by_id.get(claim["claim_id"], {}).get("reason") if claim else None) \
            or "the adjudication pass did not affirm this non-person classification"
        if claim is not None:
            refute(claim, reason)
        refusals.append({"unit": unit_obj(key), "reason": reason, "refused_by": "adjudication"})

    for person in verdicts.get("people") or []:
        pid = person["person_id"]
        person_claims = claims_by_person.get(pid, [])
        identity_claim = next((c for c in person_claims if c["kind"] == "person"), None)
        if identity_claim is None or not affirmed(identity_claim["claim_id"]):
            reason = (
                verdict_by_id.get(identity_claim["claim_id"], {}).get("reason")
                if identity_claim
                else "no person claim was projected"
            ) or "not affirmed"
            # Refuse rather than split. A survivor of a rejected merge was
            # never itself adjudicated, so emitting one would put back exactly
            # the unadjudicated person record the claim existed to prevent.
            for u in person["units"]:
                refusals.append(
                    {
                        "unit": {"source_form": u["source_form"], "sense_id": u["sense_id"]},
                        "reason": reason,
                        "refused_by": "adjudication",
                    }
                )
            for claim in person_claims:
                if claim is identity_claim:
                    continue
                refute(claim, "owner_identity_not_affirmed")
            if identity_claim is not None:
                refute(identity_claim, reason)
            continue

        status = person["identity_status"]
        status_reason = person["identity_status_reason"]
        claim = next((c for c in person_claims if c["kind"] == "identity_status"), None)
        if claim is None or not affirmed(claim["claim_id"]):
            # Unaffirmed in EITHER direction lands on `contested` with the
            # adjudicator's own reason. `contested` is the safe status, but its
            # REASON is an assertion like any other -- and the reason a
            # genealogy reader most needs to trust, since the whole point of
            # the split is that "identity genuinely contested" is not "few
            # mentions". Carrying an unaffirmed reason through would publish
            # exactly that conflation.
            if claim is not None:
                refute(claim, verdict_by_id[claim["claim_id"]].get("reason") or "not affirmed")
            status = "contested"
            # A DETERMINISTIC sentence, not the adjudicator's own prose. Pass
            # B's reason is a refutation, and nothing affirmed it; putting it in
            # a live factual field would publish unchecked prose in exactly the
            # place this claim exists to protect -- and the adjudicator's
            # wording could carry the scarcity-for-identity conflation straight
            # back in. It is kept, verbatim, in `refuted_claims[]`, where it is
            # labelled as what it is.
            status_reason = ("the adjudication pass did not affirm the stated identity status; "
                             "see refuted_claims[] for its reason")

        kept_surfaces = []
        for claim in person_claims:
            if claim["kind"] != "printed_surface":
                continue
            if affirmed(claim["claim_id"]):
                kept_surfaces.append(claim["payload"]["surface"])
            else:
                refute(claim, verdict_by_id[claim["claim_id"]].get("reason") or "not affirmed")

        kept = {"relation": [], "place": [], "date": []}
        for claim in person_claims:
            if claim["kind"] not in kept:
                continue
            if affirmed(claim["claim_id"]):
                kept[claim["kind"]].append({**claim["payload"], "claim_id": claim["claim_id"]})
            else:
                refute(claim, verdict_by_id[claim["claim_id"]].get("reason") or "not affirmed")

        units_out = []
        mentions = []
        attributable = []
        truncated = False
        for u in person["units"]:
            prep_unit = by_key[unit_key(u["source_form"], u["sense_id"])]
            units_out.append(
                {
                    "source_form": u["source_form"],
                    "sense_id": u["sense_id"],
                    "canonical_target_form": prep_unit.get("canonical_target_form"),
                    "disambiguator": prep_unit.get("disambiguator"),
                }
            )
            truncated = truncated or bool(prep_unit.get("contexts_truncated"))
            if prep_unit.get("attributable"):
                attributable.append(prep_unit)
                mentions.extend(prep_unit.get("mentions") or [])

        people_out.append(
            {
                "person_id": pid,
                "display_name": person["display_name"],
                "units": units_out,
                "identity_note": person["identity_note"],
                "identity_status": status,
                "identity_status_reason": status_reason,
                "printed_forms": [],  # filled by B4
                "mention_count": sum(len(u.get("mentions") or []) for u in attributable) if attributable else None,
                "mentions": mentions,
                # The locator travels INSIDE the claim payload rather than
                # being matched back to the verdict by (type, quote) -- two
                # claims of one type quoting the same sentence are legal, and
                # a re-match would silently pick the first.
                "relations": [
                    {
                        "claim_id": p["claim_id"],
                        "type": p["claim"]["type"],
                        **({"to_person_id": p["claim"]["to_person_id"]} if "to_person_id" in p["claim"] else {}),
                        **({"to_unregistered": p["claim"]["to_unregistered"]} if "to_unregistered" in p["claim"] else {}),
                        "quote": p["quote"],
                        "locator": p["locator"],
                    }
                    for p in kept["relation"]
                ],
                "places": [
                    {
                        "role": p["claim"]["role"],
                        "name": p["claim"]["name"],
                        "quote": p["quote"],
                        "locator": p["locator"],
                    }
                    for p in kept["place"]
                ],
                "dates": [
                    {
                        "kind": p["claim"]["kind"],
                        "value": p["claim"]["value"],
                        "quote": p["quote"],
                        "locator": p["locator"],
                    }
                    for p in kept["date"]
                ],
                "evidence_truncated": truncated,
                "_surfaces": kept_surfaces,
            }
        )

    # B5, AFTER B3 rather than before it: referential integrity has to be
    # judged against the surviving cast. `A -> B` can be affirmed while B's own
    # identity claim is not, and B is then removed -- leaving an edge pointing
    # at a person the registry does not contain.
    surviving = {person["person_id"] for person in people_out}
    for person in people_out:
        kept_relations = []
        for relation in person["relations"]:
            target = relation.get("to_person_id")
            # `claim_id` travels this far only so a refutation can name the
            # exact claim; it is not part of the emitted edge.
            emitted = {k: v for k, v in relation.items() if k != "claim_id"}
            if target is None or target in surviving:
                kept_relations.append(emitted)
                continue
            refuted_claims.append(
                {
                    "claim_id": relation["claim_id"],
                    "kind": "relation",
                    "person_id": person["person_id"],
                    "summary": f"{relation['type']} -> {target}",
                    "reason": "relation_target_identity_not_affirmed",
                }
            )
        person["relations"] = kept_relations

    for person in people_out:
        if person["mention_count"] is None:
            person["mention_count_reason"] = (
                "no unit of this person carries attributable occurrences -- an adjudicated homonym "
                "split, a fold-key collision, or a form the occurrence engine does not index"
            )

    # Anything Pass A refused, plus every review_queue row, joins refusals[].
    for row in verdicts.get("refusals") or []:
        key = unit_key(row["unit"]["source_form"], row["unit"]["sense_id"])
        prep_unit = by_key[key]
        refusals.append(
            {
                "unit": unit_obj(key),
                "reason": row["reason"] if not prep_unit.get("refusal_only") else
                (prep_unit.get("note") or row["reason"]),
                "refused_by": "canon_review_queue" if prep_unit.get("refusal_only") else "pass_a",
            }
        )

    # B4 counts.
    corpus = assembled_target_text(nodestream)
    # Every target string the RENDERER would consume, not merely every one this
    # registry owns: `build_entity_index` alternates over the whole canon and
    # never branches on `is_proper_name`, so an entry the project declared
    # realia still consumes its span in the delivered text. Omitting those
    # would let a short person-surface absorb an occurrence of a longer form
    # that simply has no owner here.
    every_target_form = [
        u.get("canonical_target_form") for u in prep.get("units") or [] if u.get("canonical_target_form")
    ] + [
        row.get("canonical_target_form")
        for row in prep.get("excluded_by_canon_declaration") or []
        if row.get("canonical_target_form")
    ]
    claimed = {}
    for person in people_out:
        for surface in person["_surfaces"] + [u["canonical_target_form"] for u in person["units"]
                                              if u["canonical_target_form"]]:
            claimed.setdefault(nfc(surface), set()).add(person["person_id"])

    shared = sorted(
        ({"surface": s, "candidates": sorted(owners)} for s, owners in claimed.items() if len(owners) > 1),
        key=lambda r: r["surface"],
    )
    shared_set = {r["surface"] for r in shared}

    counted = count_surfaces(
        sorted(s for s in claimed if s not in shared_set),
        every_target_form,
        corpus,
        args.surface_boundary,
    )

    boundary_ambiguous = 0
    not_found = 0
    for person in people_out:
        rows = []
        seen_surface = set()
        for surface in person["_surfaces"] + [u["canonical_target_form"] for u in person["units"]
                                              if u["canonical_target_form"]]:
            key = nfc(surface)
            if key in seen_surface:
                continue
            seen_surface.add(key)
            if key in shared_set:
                continue
            result = counted.get(key) or {"count": None, "status": "not_found_in_target_text",
                                          "substring_count": 0}
            rows.append({"surface": key, **result})
            if result["status"] == "boundary_ambiguous":
                boundary_ambiguous += 1
            elif result["status"] == "not_found_in_target_text":
                not_found += 1
        person["printed_forms"] = sorted(rows, key=lambda r: (-(r["count"] or 0), r["surface"]))
        del person["_surfaces"]

    unattributed = [
        {"unit": u["unit"], "reason": u["occurrences_reason"]}
        for u in prep.get("units") or []
        if not u.get("attributable") and u.get("occurrences_reason")
    ]

    registry = {
        "schema_version": 1,
        "provenance": {
            "input_sha256": prep_digest,
            "nodestream_sha256": nodestream_digest,
            "manifest_sha256": manifest_digest,
            "verdicts_sha256": verdicts_digest,
            "claims_sha256": claims_digest,
            "assembly_currency": "not_bound",
            "surface_boundary": args.surface_boundary,
        },
        "people": people_out,
        "refusals": sorted(refusals, key=lambda r: (r["unit"]["source_form"], str(r["unit"]["sense_id"]))),
        "non_person_forms": non_person_out,
        "refuted_claims": refuted_claims,
        "shared_printed_forms": shared,
        "unattributed_units": unattributed,
        "excluded_by_canon_declaration": prep.get("excluded_by_canon_declaration") or [],
        "summary": {
            "people": len(people_out),
            "refusals": len(refusals),
            "non_person_forms": len(non_person_out),
            "refuted_claims": len(refuted_claims),
            "identity_status": {
                "confirmed": sum(1 for p in people_out if p["identity_status"] == "confirmed"),
                "contested": sum(1 for p in people_out if p["identity_status"] == "contested"),
            },
            "units_total": len(prep.get("units") or []),
            "boundary_ambiguous_surfaces": boundary_ambiguous,
            "not_found_surfaces": not_found,
            "people_with_truncated_evidence": sum(1 for p in people_out if p["evidence_truncated"]),
        },
    }

    validate_or_raise(load_validator(schema_dir, REGISTRY_SCHEMA), registry,
                      "registry_schema_invalid", "the person registry this run would emit")

    write_json(durable_root / "registry" / "person_registry.json", registry)
    (durable_root / "registry" / "PEOPLE.md").write_text(render_people_md(registry), encoding="utf-8")

    return {"success": True, "mode": "build", **registry["summary"],
            "input_sha256": prep_digest, "verdicts_sha256": verdicts_digest,
            "claims_sha256": claims_digest}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="person_registry.py",
        description="W9r: the opt-in person-registry pass (#550). Writes NEW artifacts only; never "
                    "touches canon.json and never enters any cache key.",
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prep", action="store_true",
                      help="read the pipeline's artifacts and emit registry/registry_input.json")
    mode.add_argument("--claims", action="store_true",
                      help="gate Pass A's verdict and project it into registry/registry_claims.json, "
                           "one independently judgeable entry per judgement")
    mode.add_argument("--build", action="store_true",
                      help="apply Pass B's adjudications and emit registry/person_registry.json + PEOPLE.md")
    p.add_argument("--durable-root", default=None,
                   help="the project's durable root (default: this script's own grandparent -- "
                        "self-anchored, never cwd)")
    p.add_argument("--profile", default=None,
                   help="path to the project's profile.yml (default: searched under the durable root, "
                        "its parent, then cwd). Only source.language.particle_config is read.")
    p.add_argument("--plugin-root", default=None,
                   help="the plugin root, when this script runs from a durable root: the registry "
                        "schemas are deliberately never copied into a durable root, so they are "
                        "resolved from {plugin-root}/assets/schemas/registry/")
    p.add_argument("--max-contexts-per-form", type=int, default=8,
                   help="how many source contexts per unit reach the model (default 8). More than this "
                        "and an EVEN SPREAD across the whole book is kept, never the first N.")
    p.add_argument("--context-chars", type=int, default=400,
                   help="approximate width of one source context window (default 400)")
    p.add_argument("--max-input-chars", type=int, default=400000,
                   help="refuse to emit a prep document larger than this (default 400000). A blunt "
                        "guard against a silently huge input, not a model-capacity check -- this "
                        "plugin does not know the dispatched model's context window.")
    p.add_argument("--max-claims-chars", type=int, default=1500000,
                   help="refuse to emit a claims document larger than this (default 1500000). Pass B "
                        "reads it whole; like --max-input-chars this is a blunt guard against a "
                        "silently huge input, not a model-capacity check.")
    p.add_argument("--surface-boundary", choices=("word", "none"), default="word",
                   help="how a printed surface must be delimited in the target text. 'word' guards "
                        "with word boundaries; 'none' is plain substring matching, for a target "
                        "language that does not space its words -- at the cost of counting an "
                        "embedded longer form for the shorter surface.")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    durable_root = Path(args.durable_root).resolve() if args.durable_root else DEFAULT_DURABLE_ROOT
    schema_dir = (
        Path(args.plugin_root).resolve() / "assets" / "schemas" / "registry"
        if args.plugin_root
        else DEFAULT_SCHEMA_DIR
    )

    handler = cmd_prep if args.prep else (cmd_claims if args.claims else cmd_build)
    try:
        payload = handler(args, durable_root, schema_dir)
    except RegistryError as exc:
        print(dumps_line({"success": False, "reason": exc.reason, "error": exc.message}))
        print(f"Error: {exc.message}", file=sys.stderr)
        return exc.code
    print(dumps_line(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
