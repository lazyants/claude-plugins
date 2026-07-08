#!/usr/bin/env python3
"""cache_key.py -- the single shared implementation of the literary-translator
plugin's composite cache key.

STATUS: new plugin hardening (never run at scale on a real project) -- see
references/ledger-and-resumability.md's confidence-level split. Treat with
care and re-verify against a real pilot run before trusting it unconditionally.

Authoritative spec: references/ledger-and-resumability.md, the "Composite
cache key -- exact 15-field structure" and "The three separate bundle
hashes -- exact membership" sections. Read those before changing anything
here -- this file's field list/byte-scopes must match that doc exactly.

A segment is reused from cache only if every one of the 15 `cache_key`
fields below matches the current run's freshly-computed values *and* its
ledger status is `converged`. A mismatch on a single field flips only that
segment to `stale` -- it never invalidates the whole book.

This script never writes anything -- it only reads profile.yml (via the
durable-root ownership marker), the durable-root copies of schemas/
scripts/templates, manifest.json, canon.json, and per-segment segpack
files, and prints hash values.

Usage
-----
    python3 cache_key.py --seg <id>
        Prints the full 15-field `cache_key` JSON object for segment <id>
        to stdout (pretty-printed, one JSON object, field order matching
        the authoritative doc literal).

    python3 cache_key.py --field <name>
        Prints just one *global* field's current value (bare string, no
        quoting) to stdout. Used by extract.py.template's two-phase
        manifest write (`source_extraction_hash`, `source_input_hash`) and
        by the glossary-pass merge step (`derivation_bundle_hash`,
        `particle_config_hash`). Also supports the one documented
        non-cache_key exception, `smoke_report_contract_hash`.
        Passing --field with a *per-segment* field name and no --seg is a
        usage error.

    python3 cache_key.py --seg <id> --field <name>
        Prints just one field's value (global or per-segment) for/within
        the context of segment <id>. A superset convenience on top of the
        two documented invocations above -- never required, always safe.

Self-anchoring: this script always lives at
``${durable_root}/scripts/cache_key.py`` and derives durable_root from its
own path -- it never assumes cwd and never takes a --durable-root flag.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import NoReturn

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only when PyYAML is absent
    yaml = None


# ---------------------------------------------------------------------------
# Self-anchoring
# ---------------------------------------------------------------------------

DURABLE_ROOT = Path(__file__).resolve().parents[1]

# The six scripts (+ two workflow templates) that make up plugin_bundle_hash.
# NEVER bootstrap_names.py/segpack.py (their own derivation_bundle_hash) and
# NEVER the four orchestration-only scripts (orchestration_bundle_hash).
PLUGIN_BUNDLE_MEMBERS = (
    "validate_draft.py",
    "canon_validate.py",
    "cache_key.py",
    "draft_sha1.py",
    "review_artifact_check.py",
    "ledger_update.py",
    "mass-translate-wf.template.js",
    "glossary-pass-wf.template.js",
)

# The two scripts that make up derivation_bundle_hash.
DERIVATION_BUNDLE_MEMBERS = ("bootstrap_names.py", "segpack.py")

# Order matches the authoritative JSON literal in
# references/ledger-and-resumability.md exactly -- do not reorder.
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

PER_SEGMENT_FIELDS = frozenset(
    {"input_sha1", "used_terms_hash", "verse_map_hash", "note_map_hash"}
)
GLOBAL_CACHE_KEY_FIELDS = frozenset(CACHE_KEY_FIELD_ORDER) - PER_SEGMENT_FIELDS

# The one documented exception: reuses this script's CLI surface but is NOT
# a 16th cache_key member -- a report-generator-version stamp for
# language_smoke_report.py, nothing more.
EXTRA_GLOBAL_FIELDS = frozenset({"smoke_report_contract_hash"})


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def fail(message: str) -> NoReturn:
    """Fail loudly, naming the problem, and exit non-zero. Never a bare
    traceback for an expected/actionable condition."""
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


# Canonical segment-id safety contract. A seg id is either an ordinary body
# id (e.g. "seg01", "seg05_blocked_regen", "segAnchor") or a translate-decision
# FRONTBACK:{id} unit (e.g. "FRONTBACK:fm01"). It is spliced into filesystem
# paths and workflow shell commands, so it MUST be a path- and shell-safe
# allowlist. Keep this identical across every consuming script.
# NOTE: re.fullmatch (NOT re.match + "$") -- in Python "$" also matches just
# before a trailing newline, so re.match(r"...$", "seg01\n") would WRONGLY pass.
_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")


def validate_seg(seg):
    """Return an error string if `seg` is not a path/shell-safe segment id,
    else None. Allows ONLY [A-Za-z0-9_] with an optional literal 'FRONTBACK:'
    prefix -- rejecting empties, path separators, '..', absolute paths, and
    every shell metacharacter."""
    if not isinstance(seg, str) or not seg:
        return "segment id must be a non-empty string."
    if not _SEG_ID_RE.fullmatch(seg):
        return (
            "segment id must match (FRONTBACK:)?[A-Za-z0-9_]+ (no path "
            f"separators, '..', or shell metacharacters); got {seg!r}."
        )
    return None


def require_yaml() -> None:
    if yaml is None:
        fail(
            "this script requires the 'PyYAML' Python package. Install "
            "with: pip install -r requirements.txt (see the "
            "literary-translator plugin's own requirements.txt for the "
            "pinned version)."
        )


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def canonical_json_bytes(obj) -> bytes:
    """Deterministic canonical JSON: sorted keys, compact separators,
    non-ASCII preserved verbatim (never escaped) so the byte content is
    stable and human-legible."""
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def read_bytes(path: Path, what: str) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        fail(f"{what} not found at {path}")
    except OSError as exc:
        fail(f"could not read {what} at {path}: {exc}")


def read_json(path: Path, what: str):
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail(f"{what} not found at {path}")
    except OSError as exc:
        fail(f"could not read {what} at {path}: {exc}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"{what} at {path} is not valid JSON: {exc}")


def concat_sorted_bytes(paths, what: str) -> bytes:
    """Sorted-by-filename, concatenated raw bytes of every path in `paths`
    -- the exact scheme used by schema_hash/prompt_hash/derivation_bundle_hash
    (never the {filename, sha1} pairing, which is source_input_hash's own
    multi-file scheme)."""
    ordered = sorted(paths, key=lambda p: p.name)
    return b"".join(read_bytes(p, what) for p in ordered)


def profile_get(profile: dict, dotted_path: str):
    """Walk a dotted key path through the parsed profile.yml, failing
    loudly (naming the exact missing field) rather than raising a bare
    KeyError/TypeError."""
    cur = profile
    parts = dotted_path.split(".")
    for i, key in enumerate(parts):
        if not isinstance(cur, dict) or key not in cur:
            fail(
                f"profile.yml is missing required field "
                f"'{'.'.join(parts[: i + 1])}'"
            )
        cur = cur[key]
    return cur


# ---------------------------------------------------------------------------
# profile.yml resolution (via the durable-root ownership marker)
# ---------------------------------------------------------------------------


def load_owner_marker(durable_root: Path) -> dict:
    marker_path = durable_root / ".literary-translator-root.json"
    if not marker_path.exists():
        fail(
            f"ownership marker not found at {marker_path} -- has Step 0a "
            "(durable-root scaffold) been run for this project yet?"
        )
    data = read_json(marker_path, "ownership marker")
    if not isinstance(data, dict) or "owner_profile_path" not in data:
        fail(
            f"ownership marker at {marker_path} is malformed -- missing "
            "'owner_profile_path'"
        )
    return data


def load_profile(durable_root: Path) -> dict:
    require_yaml()
    marker = load_owner_marker(durable_root)
    profile_path = Path(marker["owner_profile_path"])
    if not profile_path.is_absolute():
        profile_path = (durable_root / profile_path).resolve()
    if not profile_path.exists():
        fail(
            f"profile.yml not found at {profile_path} (resolved from the "
            "ownership marker's owner_profile_path)"
        )
    # require_yaml() above exits the process if the import failed, but that
    # guard lives in a separate function -- reassert it here so the type
    # checker (and any future reader) can see that `yaml` is guaranteed
    # non-None at the point it's actually used.
    assert yaml is not None, "require_yaml() should have exited already"
    try:
        with profile_path.open("r", encoding="utf-8") as fh:
            profile = yaml.safe_load(fh)
    except OSError as exc:
        fail(f"could not read profile.yml at {profile_path}: {exc}")
    if not isinstance(profile, dict):
        fail(f"profile.yml at {profile_path} did not parse to a mapping")
    return profile


# ---------------------------------------------------------------------------
# Global field computations
# ---------------------------------------------------------------------------


def compute_pipeline_version(profile: dict, durable_root: Path) -> str:
    # Read directly, verbatim, from project.pipeline_version -- not
    # computed, just copied through (NOT a hash despite the field's
    # position in the "cache_key" object).
    return str(profile_get(profile, "project.pipeline_version"))


def compute_style_contract_hash(profile: dict, durable_root: Path) -> str:
    style_bible_path = durable_root / "style_bible.md"
    raw = read_bytes(style_bible_path, "style_bible.md")

    def find_unique_marker(marker: bytes, name: str) -> int:
        count = raw.count(marker)
        if count == 0:
            fail(
                f"style_bible.md is missing the {name} marker "
                f"({marker.decode()}) required to compute style_contract_hash"
            )
        if count > 1:
            fail(
                f"style_bible.md has {count} {name} markers -- "
                "expected exactly one"
            )
        return raw.find(marker)

    begin_marker = b"<!-- STYLE_CONTRACT_BEGIN -->"
    end_marker = b"<!-- STYLE_CONTRACT_END -->"
    begin_idx = find_unique_marker(begin_marker, "STYLE_CONTRACT_BEGIN") + len(begin_marker)
    end_idx = find_unique_marker(end_marker, "STYLE_CONTRACT_END")

    if end_idx < begin_idx:
        fail(
            "style_bible.md's STYLE_CONTRACT_END marker precedes its "
            "STYLE_CONTRACT_BEGIN marker -- markers are out of order"
        )

    return sha1_hex(raw[begin_idx:end_idx])


def compute_schema_hash(profile: dict, durable_root: Path) -> str:
    schemas_dir = durable_root / "schemas"
    paths = [
        schemas_dir / "draft.schema.json",
        schemas_dir / "review.schema.json",
        schemas_dir / "segpack.schema.json",
    ]
    blob = concat_sorted_bytes(paths, "a project-local schema file (schemas/)")
    return sha1_hex(blob)


def compute_prompt_hash(profile: dict, durable_root: Path) -> str:
    paths = [
        durable_root / "translate_TASK.md",
        durable_root / "review_TASK.md",
    ]
    blob = concat_sorted_bytes(paths, "an instantiated prompt file")
    return sha1_hex(blob)


def compute_agent_config_hash(profile: dict, durable_root: Path) -> str:
    obj = {
        "effort": profile_get(profile, "engine.effort"),
        "max_fix_rounds": profile_get(profile, "engine.max_fix_rounds"),
    }
    return sha1_hex(canonical_json_bytes(obj))


def compute_profile_semantics_hash(profile: dict, durable_root: Path) -> str:
    # Exactly these six named fields -- no more, no fewer. Deliberately
    # does not duplicate effort/max_fix_rounds (agent_config_hash's job).
    obj = {
        "source_lang": profile_get(profile, "source.language.code"),
        "target_lang": profile_get(profile, "target.language.code"),
        "verse_policy_mode": profile_get(profile, "verse_policy.mode"),
        "verse_policy_threshold_lines": profile_get(
            profile, "verse_policy.threshold_lines"
        ),
        "apparatus_policy": profile_get(profile, "footnotes.apparatus_policy"),
        "untranslated_sentinel": profile_get(
            profile, "validation.untranslated_sentinel"
        ),
    }
    return sha1_hex(canonical_json_bytes(obj))


def resolve_particle_config_path(profile: dict, durable_root: Path) -> Path:
    value = profile_get(profile, "source.language.particle_config")
    if not isinstance(value, str) or "/" in value or "\\" in value or ".." in value:
        fail(
            "source.language.particle_config must be a bare filename "
            f"(no path separators, no '..'); got {value!r}"
        )
    return durable_root / "languages" / value


def compute_particle_config_hash(profile: dict, durable_root: Path) -> str:
    particle_path = resolve_particle_config_path(profile, durable_root)
    raw = read_bytes(particle_path, "the resolved particle_config file")
    return sha1_hex(raw)


def resolve_extractor_path(profile: dict, durable_root: Path) -> Path:
    fmt = profile_get(profile, "source.format")
    if fmt in ("gutenberg_epub", "plain_text"):
        return durable_root / "extract.py"
    if fmt == "custom":
        extractor_path = profile_get(profile, "source.adapter_config.custom.extractor_path")
        if not extractor_path:
            fail(
                "source.adapter_config.custom.extractor_path is not set yet -- "
                "cannot compute source_extraction_hash until the custom "
                "extractor has been co-designed and pointed at"
            )
        if not isinstance(extractor_path, str) or extractor_path.startswith("/") or ".." in extractor_path:
            fail(
                "source.adapter_config.custom.extractor_path must be a "
                f"relative path with no '..' segments; got {extractor_path!r}"
            )
        return durable_root / "scripts" / "custom_extractors" / extractor_path
    fail(f"unknown source.format {fmt!r}")


def compute_source_extraction_hash(profile: dict, durable_root: Path) -> str:
    fmt = profile_get(profile, "source.format")
    adapter_config = profile_get(profile, f"source.adapter_config.{fmt}")
    obj = {"format": fmt, "adapter_config": adapter_config}
    extractor_path = resolve_extractor_path(profile, durable_root)
    extractor_bytes = read_bytes(extractor_path, "the resolved extractor file")
    combined = canonical_json_bytes(obj) + extractor_bytes
    return sha1_hex(combined)


def load_manifest(durable_root: Path) -> dict:
    manifest_path = durable_root / "manifest.json"
    return read_json(manifest_path, "manifest.json")


def resolve_manifest_input_path(entry: str, durable_root: Path) -> Path:
    candidate = Path(entry)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    joined = durable_root / entry
    if joined.exists():
        return joined
    if candidate.exists():
        return candidate
    fail(
        f"manifest.json's source_inputs[] names {entry!r}, which does not "
        f"resolve to an existing file (tried {candidate} and {joined})"
    )


def compute_source_input_hash(profile: dict, durable_root: Path) -> str:
    fmt = profile_get(profile, "source.format")
    source_path = profile_get(profile, "source.path")
    manifest = load_manifest(durable_root)
    source_inputs = manifest.get("source_inputs")
    if not isinstance(source_inputs, list) or not source_inputs:
        fail("manifest.json is missing a non-empty 'source_inputs' array")

    if fmt in ("gutenberg_epub", "plain_text"):
        # Single-file case: source_bytes_sha1 is the sha1 of that one
        # file's raw bytes directly -- never the {filename, sha1} pairing
        # (that scheme is custom's own multi-file case, below).
        file_path = resolve_manifest_input_path(source_inputs[0], durable_root)
        source_bytes_sha1 = sha1_hex(read_bytes(file_path, "the source file"))
    else:
        # custom, potentially multi-file: sha1 of canonical JSON
        # [{filename, sha1(bytes)}], sorted by filename -- filename is
        # part of what's hashed, never just the sort key, so a
        # byte-identical swap-in under a different name still changes the
        # hash.
        pairs = []
        for entry in source_inputs:
            file_path = resolve_manifest_input_path(entry, durable_root)
            pairs.append(
                {"filename": entry, "sha1": sha1_hex(read_bytes(file_path, f"source input {entry}"))}
            )
        pairs.sort(key=lambda p: p["filename"])
        source_bytes_sha1 = sha1_hex(canonical_json_bytes(pairs))

    obj = {"source_path": source_path, "source_bytes_sha1": source_bytes_sha1}
    return sha1_hex(canonical_json_bytes(obj))


def compute_derivation_bundle_hash(profile: dict, durable_root: Path) -> str:
    paths = [durable_root / "scripts" / name for name in DERIVATION_BUNDLE_MEMBERS]
    blob = concat_sorted_bytes(paths, "a derivation-bundle script")
    return sha1_hex(blob)


def compute_plugin_bundle_hash(profile: dict, durable_root: Path) -> str:
    # NOT recomputed here -- Step 0a computes this exactly once per run
    # (at the moment it copies scripts into durable_root) and writes it to
    # this marker file. cache_key.py reads it back rather than re-hashing
    # the bundle on every single segment.
    marker_path = durable_root / "runs" / ".plugin_bundle_hash"
    raw = read_bytes(marker_path, "the plugin_bundle_hash marker file")
    value = raw.decode("utf-8").strip()
    if not value:
        fail(f"{marker_path} is empty -- has Step 0a run for this project?")
    return value


def compute_smoke_report_contract_hash(profile: dict, durable_root: Path) -> str:
    # Deliberate exception: NOT a 16th cache_key member. Sha1 of
    # language_smoke_report.py's own bytes, reusing this CLI purely so
    # this one extra hash doesn't need a duplicate implementation.
    path = durable_root / "scripts" / "language_smoke_report.py"
    return sha1_hex(read_bytes(path, "language_smoke_report.py"))


GLOBAL_FIELD_FUNCS = {
    "pipeline_version": compute_pipeline_version,
    "style_contract_hash": compute_style_contract_hash,
    "schema_hash": compute_schema_hash,
    "prompt_hash": compute_prompt_hash,
    "agent_config_hash": compute_agent_config_hash,
    "profile_semantics_hash": compute_profile_semantics_hash,
    "particle_config_hash": compute_particle_config_hash,
    "source_extraction_hash": compute_source_extraction_hash,
    "source_input_hash": compute_source_input_hash,
    "derivation_bundle_hash": compute_derivation_bundle_hash,
    "plugin_bundle_hash": compute_plugin_bundle_hash,
    "smoke_report_contract_hash": compute_smoke_report_contract_hash,
}


# ---------------------------------------------------------------------------
# Per-segment field computations
# ---------------------------------------------------------------------------


def load_segpack(durable_root: Path, seg: str) -> dict:
    path = durable_root / "segments" / f"segpack_{seg}.json"
    return read_json(path, f"segpack for segment {seg!r}")


def compute_input_sha1(segpack: dict) -> str:
    blocks = sorted(segpack.get("blocks", []), key=lambda b: b["order_index"])
    parts = []
    for block in blocks:
        if "source_html" in block:
            parts.append(block["source_html"])
        elif "plain_text" in block:
            parts.append(block["plain_text"])
        else:
            fail(
                f"segpack block {block.get('id')!r} has neither "
                "'source_html' nor 'plain_text'"
            )
    return sha1_hex("".join(parts).encode("utf-8"))


def load_canon(durable_root: Path) -> dict:
    path = durable_root / "canon.json"
    return read_json(path, "canon.json")


def compute_used_terms_hash(durable_root: Path, segpack: dict) -> str:
    canon = load_canon(durable_root)
    entries = canon.get("entries", {})
    names = sorted(set(segpack.get("canon_names", [])) | set(segpack.get("new_names", [])))
    referenced = {name: entries[name] for name in names if name in entries}
    return sha1_hex(canonical_json_bytes(referenced))


def compute_verse_map_hash(segpack: dict) -> str:
    verses = segpack.get("verses", [])
    projected = [
        {"vid": v["vid"], "placeholder": v["placeholder"], "parent_block": v["parent_block"]}
        for v in verses
    ]
    return sha1_hex(canonical_json_bytes(projected))


def compute_note_map_hash(segpack: dict) -> str:
    footnotes = segpack.get("footnotes", [])
    projected = [{"n": f["n"], "source_text": f["source_text"]} for f in footnotes]
    return sha1_hex(canonical_json_bytes(projected))


PER_SEGMENT_FIELD_FUNCS = {
    "input_sha1": lambda durable_root, segpack: compute_input_sha1(segpack),
    "used_terms_hash": compute_used_terms_hash,
    "verse_map_hash": lambda durable_root, segpack: compute_verse_map_hash(segpack),
    "note_map_hash": lambda durable_root, segpack: compute_note_map_hash(segpack),
}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def compute_full_cache_key(profile: dict, durable_root: Path, seg: str) -> dict:
    segpack = load_segpack(durable_root, seg)
    result = {}
    for field in CACHE_KEY_FIELD_ORDER:
        if field in PER_SEGMENT_FIELDS:
            result[field] = PER_SEGMENT_FIELD_FUNCS[field](durable_root, segpack)
        else:
            result[field] = GLOBAL_FIELD_FUNCS[field](profile, durable_root)
    return result


def compute_one_field(field: str, profile_loader, durable_root: Path, seg):
    if field in PER_SEGMENT_FIELDS:
        if seg is None:
            fail(
                f"'{field}' is a per-segment cache_key field -- pass --seg "
                "to compute it"
            )
        segpack = load_segpack(durable_root, seg)
        return PER_SEGMENT_FIELD_FUNCS[field](durable_root, segpack)

    if field in GLOBAL_CACHE_KEY_FIELDS or field in EXTRA_GLOBAL_FIELDS:
        profile = profile_loader()
        return GLOBAL_FIELD_FUNCS[field](profile, durable_root)

    fail(
        f"unknown --field {field!r}. Valid fields: "
        + ", ".join(sorted(GLOBAL_CACHE_KEY_FIELDS | PER_SEGMENT_FIELDS | EXTRA_GLOBAL_FIELDS))
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Print the literary-translator plugin's composite cache_key "
            "(or a single named field of it)."
        )
    )
    parser.add_argument(
        "--seg",
        default=None,
        help="Segment id, e.g. seg05. Required for the full 15-field JSON "
        "output and for any per-segment --field.",
    )
    parser.add_argument(
        "--field",
        default=None,
        help="Print just this one field's current value instead of the "
        "full JSON object.",
    )
    args = parser.parse_args()

    if args.seg is None and args.field is None:
        parser.error("either --seg or --field (or both) is required")

    if args.seg is not None:
        seg_error = validate_seg(args.seg)
        if seg_error is not None:
            fail(seg_error)

    durable_root = DURABLE_ROOT

    # profile.yml is only loaded lazily, on first actual need -- some
    # per-segment fields (input_sha1, verse_map_hash, note_map_hash) don't
    # need it at all, and used_terms_hash needs canon.json, not profile.yml.
    cached_profile = None

    def profile_loader():
        nonlocal cached_profile
        if cached_profile is None:
            cached_profile = load_profile(durable_root)
        return cached_profile

    if args.field is not None:
        value = compute_one_field(args.field, profile_loader, durable_root, args.seg)
        print(value)
        return 0

    # args.seg is not None and args.field is None: full 15-field JSON.
    # (Guaranteed by the "either --seg or --field is required" check above,
    # combined with the args.field is not None early-return just above --
    # reassert it directly here so this is both self-documenting and
    # verifiable by the type checker.)
    if args.seg is None:
        parser.error("--seg is required to print the full cache_key JSON")
    profile = profile_loader()
    cache_key = compute_full_cache_key(profile, durable_root, args.seg)
    print(json.dumps(cache_key, indent=2, sort_keys=False, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
