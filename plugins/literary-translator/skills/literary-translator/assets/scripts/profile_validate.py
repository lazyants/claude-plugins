#!/usr/bin/env python3
"""profile_validate.py -- Step 0: read + validate ``profile.yml``.

Authoritative spec: SKILL.md's "Step 0 -- Read + validate profile.yml"
section, cross-checked against ``assets/schemas/profile.schema.json`` and
``assets/profile.example.yml``. Read those before changing anything here.

**THE ONE SCRIPT NEVER COPIED TO ``durable_root``.** Every other script in
this plugin gets physically copied to ``${durable_root}/scripts/`` by Step 0a
and self-anchors relative to ITS OWN location under durable_root. This script
runs *before* Step 0a exists to do that copying -- there is no durable-root
copy of it yet, and there never will be one. It is always invoked directly
from the plugin's own install path:

    python3 {{PLUGIN_ROOT}}/assets/scripts/profile_validate.py \\
        --profile .claude/literary-translator/profile.yml

...run by the orchestrating Claude session itself, not by a generated
workflow script. For the exact same reason, it loads
``assets/profile.example.yml`` and ``assets/schemas/profile.schema.json``
straight out of the plugin's own ``assets/`` directory (self-anchored via
``Path(__file__).resolve().parents[1]`` -- one level up from this script's
own ``assets/scripts/`` directory, giving ``assets/``) rather than a
durable-root copy of either.

Order of operations (numbered to match SKILL.md's Step 0 list exactly):

  1. Existence check FIRST, before any dependency preflight or validation.
     If the profile path is absent, copy the shipped
     ``assets/profile.example.yml`` there verbatim and HALT -- an existing,
     filled-in profile is never touched again (checked fresh every run).
  2. Dependency preflight: ``import yaml`` and ``import jsonschema``, each in
     its own try/except, with an actionable ``pip install`` message naming
     the missing package.
  3. Parse YAML via ``yaml.safe_load`` (never ``yaml.load``). Reject a
     non-mapping document. Check ``profile_version`` against a hardcoded
     current-version constant, with a migration hint on mismatch.
  4. Unknown top-level keys are FATAL by default, naming the exact key --
     except keys under the reserved ``x_*`` namespace (forward-compat
     extension point), which are silently allowed.
  5. Validate whole-file shape via
     ``jsonschema.Draft202012Validator(profile.schema.json,
     format_checker=jsonschema.FormatChecker())``.
  6. Only once schema validation passes, run the procedural checks a schema
     alone cannot express: ``source.path`` existence; ``project.durable_root``'s
     PARENT existence/writability/not-under-tmp-or-scratchpad;
     ``output.destination``'s parent, checked only when it resolves OUTSIDE
     durable_root. (``source.language.particle_config``'s file existence is
     deliberately NOT checked here -- deferred to the end of Step 0a, since
     the preset hasn't been copied into the project yet on a fresh project.)
  7. Whole-profile placeholder-substring scan: every string value anywhere in
     the parsed document, not a named subset of fields, is checked against
     every literal placeholder ``assets/profile.example.yml`` ships (the
     book-title placeholder, the two ``/ABS/PATH/TO/...`` path placeholders,
     and every ``CHOOSE_*``-prefixed enum sentinel) -- FATAL if any survive.
  8. ``adapter_config.plain_text.segmentation.heading_regex``: compilability
     check (``re.compile`` in try/except) whenever ``method: heading_regex``
     is the active segmentation method -- FATAL on ``re.error``. Non-fatal
     cross-field WARNING when the *unselected* method's own sibling field is
     still non-null (dead configuration left lying around).
  9. ``source.format: custom`` selected -> non-fatal WARNING naming it
     experimental/unpiloted, pointing at
     ``references/source-format-adapters/custom.md``.
  10. ``source.language.particle_config``: FATAL, field-named rejection of
      any value containing a forward slash, a backslash, a ``..`` segment, or
      an absolute-path prefix -- checked BEFORE any path-join is attempted.
  11. ``source.language.smoke_test.report_path``: FATAL rejection of any
      value containing the literal substring ``..`` anywhere -- checked
      BEFORE any path-join is attempted.
  12. On a RESUMED project: ``translate_TASK.md`` / ``review_TASK.md`` /
      ``glossary_TASK.md`` under durable_root, if they exist, each get their
      leading ``<!-- PROMPT_CONTRACT_VERSION: N -->`` HTML-comment marker
      checked against a hardcoded ``CURRENT_PROMPT_CONTRACT_VERSION``
      constant. Four separately-named fatal states: missing marker (treated
      as version 0, therefore always stale), a malformed non-integer value, a
      duplicated marker with two conflicting values, and a marker present but
      not on the file's first non-blank line -- plus the ordinary stale-vs-
      current version mismatch once the marker itself is well-formed.
  13. Same four-state (plus mismatch) check for ``extract.py`` under
      durable_root, if it exists, against its own leading
      ``# EXTRACTOR_CONTRACT_VERSION: N`` **Python comment** (not an
      HTML comment -- this file must stay valid, importable Python), compared
      against a hardcoded ``CURRENT_EXTRACTOR_CONTRACT_VERSION`` constant.

Every violation is printed as its own field-named, actionable line. The
script exits non-zero if ANY fatal violation was found (across every step
above -- this is a "collect everything, then report everything" validator,
not a stop-at-the-first-error one), 0 if clean (warnings alone do not fail
the run).

Exit codes: 0 = clean (see stdout for any warnings); 1 = one or more fatal
validation failures (see stderr); 2 = usage or environment error (bad CLI
args, missing dependency).
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Self-anchoring: this script is the one deliberate exception to "every
# script lives under ${durable_root}/scripts/ and self-anchors via
# Path(__file__).resolve().parents[1]" -- it lives at the PLUGIN'S OWN
# ``assets/scripts/`` directory and is never copied anywhere else, so its
# parents[1] gives the plugin's ``assets/`` root instead of a durable_root.
# It never assumes cwd and never takes a --plugin-root flag.
# ---------------------------------------------------------------------------
ASSETS_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PROFILE_PATH = ASSETS_ROOT / "profile.example.yml"
SCHEMA_PATH = ASSETS_ROOT / "schemas" / "profile.schema.json"

# Deferred dependency handles -- populated by _dependency_preflight(), never
# imported at module load time (the "profile.yml is absent" branch must not
# require either package to be installed at all; see step 1 above).
yaml = None
jsonschema = None

# --- Hardcoded version constants -------------------------------------------
# Bump CURRENT_PROFILE_VERSION only in lockstep with profile.schema.json's
# own `profile_version: {"const": N}`. Bump CURRENT_PROMPT_CONTRACT_VERSION
# whenever translate_TASK.template.md / review_TASK.template.md /
# glossary_TASK.template.md's prompt CONTRACT (required fields, role
# boundaries) changes in a way that makes an old, hand-adapted copy stale.
# Bump CURRENT_EXTRACTOR_CONTRACT_VERSION whenever extract.py.template's
# OUTPUT CONTRACT (manifest.json shape) changes in a way that makes an old,
# hand-adapted copy stale. All three are plugin-build constants, never
# profile.yml fields.
CURRENT_PROFILE_VERSION = 1
CURRENT_PROMPT_CONTRACT_VERSION = 1
CURRENT_EXTRACTOR_CONTRACT_VERSION = 1

# The exact top-level keys profile.schema.json's own `required` list names --
# kept as a plain constant here (rather than re-derived from the schema at
# runtime) so the "unknown top-level key" check (step 4) can run, with a
# friendly field-named message, BEFORE the heavier jsonschema pass (step 5).
KNOWN_TOP_LEVEL_KEYS = frozenset({
    "profile_version",
    "project",
    "source",
    "target",
    "verse_policy",
    "engine",
    "footnotes",
    "glossary",
    "validation",
    "output",
})
RESERVED_KEY_PREFIX = "x_"

# Every literal placeholder string assets/profile.example.yml ships,
# transcribed verbatim from that file (read it directly, don't re-derive).
# Substring match -- e.g. "/ABS/PATH/TO/YOUR_SOURCE.epub" still contains
# "/ABS/PATH/TO/YOUR_SOURCE", and "/ABS/PATH/TO/YOUR_PROJECT/out/" still
# contains "/ABS/PATH/TO/YOUR_PROJECT".
PLACEHOLDER_SUBSTRINGS = (
    "YOUR BOOK TITLE HERE",
    "/ABS/PATH/TO/YOUR_PROJECT",
    "/ABS/PATH/TO/YOUR_SOURCE",
)
# The example ships three separate CHOOSE_-prefixed sentinels
# (verse_detection, footnotes, glossary.research_mode) -- rather than
# enumerate each one by hand (and silently miss a future fourth), any string
# value starting with this prefix anywhere in the document is rejected.
CHOOSE_PREFIX = "CHOOSE_"

TMP_OR_SCRATCHPAD_MARKERS = frozenset({"tmp", "temp", "scratchpad"})
# Narrow, default-off override for check_durable_root()'s tmp/scratchpad
# rejection -- see that function's docstring. Set to "1" to accept a
# durable_root that resolves under a tmp/temp/scratchpad path component;
# any other value (including unset) leaves the rejection in force.
ALLOW_TMP_ROOT_ENV_VAR = "LT_PROFILE_VALIDATE_ALLOW_TMP_ROOT"

PROMPT_CONTRACT_MARKER_RE = re.compile(r"^\s*<!--\s*PROMPT_CONTRACT_VERSION:\s*(.+?)\s*-->\s*$")
EXTRACTOR_CONTRACT_MARKER_RE = re.compile(r"^\s*#\s*EXTRACTOR_CONTRACT_VERSION:\s*(.+?)\s*$")

RESUMED_PROMPT_CONTRACT_FILENAMES = (
    "translate_TASK.md",
    "review_TASK.md",
    "glossary_TASK.md",
)


# ---------------------------------------------------------------------------
# Step 1/2: existence check + dependency preflight
# ---------------------------------------------------------------------------

def ensure_profile_exists(profile_path: Path) -> bool:
    """Step 1. Returns True if `profile_path` already existed (caller may
    proceed). If absent, copies the shipped example there and returns False
    (caller must halt) -- checked fresh on every invocation, so an existing,
    filled-in profile is NEVER touched again."""
    if profile_path.exists():
        return True
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(EXAMPLE_PROFILE_PATH, profile_path)
    return False


def _find_requirements_txt(max_up: int = 6):
    """Best-effort resolution of the plugin's own requirements.txt for an
    actionable pip-install message -- walks up from this script's own
    location (never assumes a fixed depth) rather than hardcoding a
    {{PLUGIN_ROOT}}-style path that may not match this install layout."""
    here = Path(__file__).resolve()
    for ancestor in list(here.parents)[:max_up]:
        candidate = ancestor / "requirements.txt"
        if candidate.is_file():
            return candidate
    return None


def _missing_dependency_message(package_name: str) -> str:
    req_path = _find_requirements_txt()
    where = str(req_path) if req_path else (
        "requirements.txt (see the literary-translator plugin's own root directory)"
    )
    return (
        f"ERROR: this plugin requires the {package_name!r} Python package. "
        f"Install with: pip install -r {where}"
    )


def dependency_preflight():
    """Step 2. Wraps `import yaml` and `import jsonschema` each in their own
    try/except, printing an actionable, package-named message and exiting
    non-zero on ImportError. Populates the module-level `yaml`/`jsonschema`
    names on success."""
    global yaml, jsonschema
    try:
        import yaml as _yaml
    except ImportError:
        print(_missing_dependency_message("PyYAML"), file=sys.stderr)
        sys.exit(2)
    try:
        import jsonschema as _jsonschema
    except ImportError:
        print(_missing_dependency_message("jsonschema"), file=sys.stderr)
        sys.exit(2)
    yaml = _yaml
    jsonschema = _jsonschema


# ---------------------------------------------------------------------------
# Step 3/4: parse + profile_version + unknown-top-level-key checks
# ---------------------------------------------------------------------------

def parse_profile_yaml(profile_path: Path):
    """Step 3 (parse half). Returns the parsed mapping, or halts (exit 1)
    naming the parse problem."""
    assert yaml is not None, (
        "dependency_preflight() must run before parse_profile_yaml() -- "
        "the yaml module is not yet loaded"
    )
    try:
        text = profile_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: could not read {profile_path}: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        profile = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        print(f"ERROR: {profile_path} is not valid YAML: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(profile, dict):
        print(
            f"ERROR: {profile_path} did not parse to a mapping "
            f"(got {type(profile).__name__}) -- profile.yml's top level must "
            f"be a YAML mapping of the documented keys.",
            file=sys.stderr,
        )
        sys.exit(1)
    return profile


def check_profile_version(profile: dict):
    """Step 3 (version half). A dedicated, friendlier-messaged check that
    runs BEFORE the heavier jsonschema pass -- an unknown/missing
    profile_version gets a migration hint, not a generic schema error."""
    version = profile.get("profile_version")
    if version != CURRENT_PROFILE_VERSION:
        return [
            f"profile_version: {version!r} is not a version this plugin build "
            f"understands (expected {CURRENT_PROFILE_VERSION}). If this profile "
            f"predates a plugin upgrade, see CHANGELOG.md for migration notes; "
            f"otherwise start from a fresh assets/profile.example.yml and "
            f"re-apply your values."
        ]
    return []


def check_unknown_top_level_keys(profile: dict):
    """Step 4. Unknown top-level keys are FATAL by default, naming the exact
    key -- except the reserved `x_*` forward-compat namespace."""
    errors = []
    for key in profile:
        if key in KNOWN_TOP_LEVEL_KEYS:
            continue
        if isinstance(key, str) and key.startswith(RESERVED_KEY_PREFIX):
            continue
        errors.append(
            f"unknown top-level key {key!r} -- not part of profile.schema.json, "
            f"and not under the reserved 'x_' forward-compat namespace. Remove "
            f"it, or rename it with an 'x_' prefix if it's a deliberate "
            f"project-local extension."
        )
    return errors


# ---------------------------------------------------------------------------
# Step 5: whole-file jsonschema validation
# ---------------------------------------------------------------------------

def load_profile_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_against_schema(profile: dict, schema: dict):
    assert jsonschema is not None, (
        "dependency_preflight() must run before validate_against_schema() -- "
        "the jsonschema module is not yet loaded"
    )
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )
    errors = sorted(validator.iter_errors(profile), key=lambda e: [str(p) for p in e.path])
    formatted = []
    for e in errors:
        location = ".".join(str(p) for p in e.path) or "<root>"
        formatted.append(f"{location}: {e.message}")
    return formatted


# ---------------------------------------------------------------------------
# Step 6: procedural checks a schema alone cannot express
# ---------------------------------------------------------------------------

def check_source_path(profile: dict):
    raw = profile["source"]["path"]
    if not raw or not Path(raw).expanduser().exists():
        return [f"source.path: does not exist: {raw!r}"]
    return []


def _resolves_under_tmp_or_scratchpad(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    return any(part.lower() in TMP_OR_SCRATCHPAD_MARKERS for part in resolved.parts)


def check_durable_root(profile: dict):
    """`project.durable_root`'s PARENT must exist and be writable, and must
    NOT resolve under a tmp/scratchpad directory. durable_root itself is NOT
    required to exist yet -- Step 0a creates it.

    The tmp/scratchpad rejection alone (never the parent-exists/writable
    checks) is skipped when the `LT_PROFILE_VALIDATE_ALLOW_TMP_ROOT`
    environment variable is exactly "1" -- a narrow, default-off override
    for ephemeral/CI/test environments that intentionally place
    durable_root under a tmp dir (e.g. pytest's tmp_path, which resolves
    under /tmp on Linux CI runners)."""
    errors = []
    raw = profile["project"]["durable_root"]
    durable_root = Path(raw).expanduser()

    allow_tmp_root = os.environ.get(ALLOW_TMP_ROOT_ENV_VAR) == "1"
    if not allow_tmp_root and _resolves_under_tmp_or_scratchpad(durable_root):
        errors.append(
            f"project.durable_root: must not resolve under a tmp/temp/"
            f"scratchpad directory (resolves to {durable_root.resolve()})"
        )

    parent = durable_root.parent
    if not parent.exists():
        errors.append(f"project.durable_root: parent directory does not exist: {parent}")
    elif not os.access(parent, os.W_OK):
        errors.append(f"project.durable_root: parent directory is not writable: {parent}")

    return errors


def check_output_destination(profile: dict):
    """`output.destination`'s parent is checked ONLY when it resolves
    OUTSIDE durable_root (the common default, inside durable_root, defers
    to Step 0a, which creates it)."""
    dest_raw = profile["output"]["destination"]
    durable_root_raw = profile["project"]["durable_root"]

    dest = Path(dest_raw).expanduser().resolve()
    durable_root = Path(durable_root_raw).expanduser().resolve()

    try:
        dest.relative_to(durable_root)
        return []  # inside durable_root -- Step 0a will create it
    except ValueError:
        pass

    errors = []
    parent = dest.parent
    if not parent.exists():
        errors.append(
            f"output.destination: parent directory does not exist: {parent} "
            f"(destination resolves outside durable_root, so Step 0a will "
            f"not auto-create it)"
        )
    elif not os.access(parent, os.W_OK):
        errors.append(f"output.destination: parent directory is not writable: {parent}")
    return errors


# ---------------------------------------------------------------------------
# Step 7: whole-profile placeholder-substring scan
# ---------------------------------------------------------------------------

def _walk_strings(obj, path=""):
    """Yields (dotted_path, string_value) for every string leaf anywhere in a
    parsed YAML/JSON-like structure (dicts, lists, scalars)."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _walk_strings(value, child_path)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _walk_strings(value, f"{path}[{index}]")
    elif isinstance(obj, str):
        yield path, obj


def scan_placeholders(profile: dict):
    """Step 7. Scans EVERY field, not a named subset -- FATAL if any value
    anywhere still contains a shipped profile.example.yml placeholder
    substring, or is still exactly one of the CHOOSE_-prefixed enum
    sentinels."""
    errors = []
    for location, value in _walk_strings(profile):
        for placeholder in PLACEHOLDER_SUBSTRINGS:
            if placeholder in value:
                errors.append(
                    f"{location}: still contains the unreplaced placeholder "
                    f"{placeholder!r} (current value: {value!r}) -- copy the "
                    f"shipped assets/profile.example.yml's comment for this "
                    f"field and replace it with a real value."
                )
        if value.startswith(CHOOSE_PREFIX):
            errors.append(
                f"{location}: still has the shipped placeholder sentinel "
                f"{value!r} -- consciously choose one of its documented "
                f"real values before proceeding."
            )
    return errors


# ---------------------------------------------------------------------------
# Step 8: plain_text.segmentation.heading_regex compilability + cross-field
# warning
# ---------------------------------------------------------------------------

def check_plain_text_segmentation(profile: dict):
    """Returns (fatal_errors, warnings)."""
    errors, warnings = [], []
    plain_text = profile["source"]["adapter_config"]["plain_text"]
    if not plain_text:
        return errors, warnings

    segmentation = plain_text.get("segmentation") or {}
    method = segmentation.get("method")
    heading_regex = segmentation.get("heading_regex")
    blank_line_threshold = segmentation.get("blank_line_threshold")

    if method == "heading_regex" and heading_regex:
        try:
            re.compile(heading_regex)
        except re.error as exc:
            errors.append(
                f"source.adapter_config.plain_text.segmentation.heading_regex: "
                f"does not compile as a regular expression ({exc}): {heading_regex!r}"
            )

    if method == "blank_line_run" and heading_regex is not None:
        warnings.append(
            "source.adapter_config.plain_text.segmentation.heading_regex is "
            "set but segmentation.method is 'blank_line_run' -- heading_regex "
            "is inert while this method is inactive; clear it to avoid "
            "confusion, or switch method if that was the intent."
        )
    elif method == "heading_regex" and blank_line_threshold is not None:
        warnings.append(
            "source.adapter_config.plain_text.segmentation.blank_line_threshold "
            "is set but segmentation.method is 'heading_regex' -- "
            "blank_line_threshold is inert while this method is inactive; "
            "clear it to avoid confusion, or switch method if that was the "
            "intent."
        )

    return errors, warnings


# ---------------------------------------------------------------------------
# Step 9: source.format: custom experimental warning
# ---------------------------------------------------------------------------

def check_custom_format_warning(profile: dict):
    if profile["source"]["format"] == "custom":
        return [
            "source.format: 'custom' is selected -- this adapter is "
            "experimental and not yet pilot-proven end-to-end; see "
            "references/source-format-adapters/custom.md before relying on "
            "it for a real project."
        ]
    return []


# ---------------------------------------------------------------------------
# Steps 10/11: path-traversal rejections
# ---------------------------------------------------------------------------

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _path_traversal_violation(value: str):
    """Returns a human-readable violation description, or None if `value` is
    a safe bare filename/relative fragment."""
    if "/" in value:
        return "must not contain a forward slash"
    if "\\" in value:
        return "must not contain a backslash"
    if ".." in value:
        return "must not contain a '..' path-traversal segment"
    if value.startswith(("/", "~")) or _WINDOWS_DRIVE_RE.match(value):
        return "must not be an absolute path"
    return None


def check_particle_config(profile: dict):
    """Step 10. Rejects (FATAL, field-named) any particle_config value
    containing a forward slash, a backslash, a '..' segment, or an
    absolute-path prefix -- BEFORE any path-join is attempted."""
    value = profile["source"]["language"]["particle_config"]
    if not isinstance(value, str):
        return []
    violation = _path_traversal_violation(value)
    if violation:
        return [
            f"source.language.particle_config: {violation} (got {value!r}) -- "
            f"this must be a bare filename, resolved as "
            f"${{durable_root}}/languages/<value>, never a path."
        ]
    return []


def check_smoke_test_report_path(profile: dict):
    """Step 11. Rejects (FATAL) any report_path value containing the literal
    substring '..' anywhere -- BEFORE any path-join is attempted."""
    value = profile["source"]["language"]["smoke_test"]["report_path"]
    # None is not a str -- one isinstance check covers both the "omitted" and
    # "wrong type" cases; wrong-type is left for the schema pass to name.
    if not isinstance(value, str):
        return []
    if ".." in value:
        return [
            f"source.language.smoke_test.report_path: must not contain a "
            f"'..' path-traversal segment anywhere (got {value!r})"
        ]
    return []


# ---------------------------------------------------------------------------
# Steps 12/13: resumed-project PROMPT_CONTRACT_VERSION / EXTRACTOR_CONTRACT_
# VERSION drift checks
# ---------------------------------------------------------------------------

def _first_non_blank_line_index(lines):
    for index, line in enumerate(lines):
        if line.strip():
            return index
    return None


def _find_marker_occurrences(lines, pattern):
    occurrences = []
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match:
            occurrences.append((index, match.group(1).strip()))
    return occurrences


def check_contract_marker(path: Path, marker_name: str, pattern, current_version: int):
    """Shared four-state (+ mismatch) check for a single file's leading
    contract-version marker, used identically for the three *_TASK.md files
    (PROMPT_CONTRACT_VERSION, HTML-comment syntax) and extract.py
    (EXTRACTOR_CONTRACT_VERSION, Python-comment syntax). Only runs at all
    when `path` exists -- a missing file just means this isn't a resumed
    project yet, not a violation."""
    if not path.is_file():
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: could not read file to check its {marker_name} marker: {exc}"]

    lines = text.splitlines()
    first_non_blank = _first_non_blank_line_index(lines)
    occurrences = _find_marker_occurrences(lines, pattern)

    if not occurrences:
        return [
            f"{path}: no leading {marker_name} marker found -- treated as "
            f"version 0, which is always stale against the current version "
            f"{current_version}. Re-apply the current template by hand "
            f"(never auto-overwrite a hand-adapted file) and add the marker "
            f"as the file's first non-blank line."
        ]

    malformed = [(idx, val) for idx, val in occurrences if not re.fullmatch(r"-?\d+", val)]
    if malformed:
        idx, val = malformed[0]
        return [
            f"{path}: {marker_name} marker on line {idx + 1} has a malformed, "
            f"non-integer value {val!r} -- expected a bare integer."
        ]

    distinct_values = {int(val) for _, val in occurrences}
    if len(occurrences) > 1 and len(distinct_values) > 1:
        return [
            f"{path}: duplicated {marker_name} marker with conflicting "
            f"values {sorted(distinct_values)} -- exactly one leading marker "
            f"is expected."
        ]

    marker_line_index = occurrences[0][0]
    if marker_line_index != first_non_blank:
        return [
            f"{path}: {marker_name} marker found on line {marker_line_index + 1}, "
            f"but that is not the file's first non-blank line (first "
            f"non-blank content is on line {(first_non_blank or 0) + 1}) -- "
            f"the marker must lead the file."
        ]

    version = distinct_values.pop()
    if version != current_version:
        return [
            f"{path}: {marker_name} is version {version}, current is "
            f"{current_version} -- stale. Re-apply the current template by "
            f"hand (never auto-overwrite) and bump the marker once migrated."
        ]
    return []


def check_resumed_contract_versions(durable_root: Path):
    errors = []
    for filename in RESUMED_PROMPT_CONTRACT_FILENAMES:
        errors.extend(
            check_contract_marker(
                durable_root / filename,
                "PROMPT_CONTRACT_VERSION",
                PROMPT_CONTRACT_MARKER_RE,
                CURRENT_PROMPT_CONTRACT_VERSION,
            )
        )
    errors.extend(
        check_contract_marker(
            durable_root / "extract.py",
            "EXTRACTOR_CONTRACT_VERSION",
            EXTRACTOR_CONTRACT_MARKER_RE,
            CURRENT_EXTRACTOR_CONTRACT_VERSION,
        )
    )
    return errors


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Step 0: read + validate .claude/literary-translator/profile.yml. "
            "Always invoked from the plugin's own install path -- never a "
            "durable-root copy."
        )
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="Path to the project's profile.yml (e.g. "
             ".claude/literary-translator/profile.yml).",
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    profile_path = Path(args.profile)

    # --- Step 1: existence check, before anything else -----------------
    if not ensure_profile_exists(profile_path):
        print(
            f"Created a starter profile at {profile_path} from "
            f"assets/profile.example.yml. Fill in every placeholder "
            f"(YOUR BOOK TITLE HERE, /ABS/PATH/TO/YOUR_PROJECT, "
            f"/ABS/PATH/TO/YOUR_SOURCE, every CHOOSE_-prefixed field), then "
            f"re-run Step 0.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Step 2: dependency preflight ------------------------------------
    dependency_preflight()

    # --- Step 3: parse + profile_version --------------------------------
    profile = parse_profile_yaml(profile_path)

    version_errors = check_profile_version(profile)
    if version_errors:
        for err in version_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    # --- Step 4: unknown top-level keys ----------------------------------
    unknown_key_errors = check_unknown_top_level_keys(profile)
    if unknown_key_errors:
        for err in unknown_key_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    # --- Step 5: whole-file schema validation ----------------------------
    schema = load_profile_schema()
    schema_errors = validate_against_schema(profile, schema)
    if schema_errors:
        for err in schema_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    # --- Steps 6-13: procedural checks (schema already passed) -----------
    fatal_errors = []
    warnings = []

    fatal_errors += check_source_path(profile)
    fatal_errors += check_durable_root(profile)
    fatal_errors += check_output_destination(profile)

    fatal_errors += scan_placeholders(profile)

    seg_errors, seg_warnings = check_plain_text_segmentation(profile)
    fatal_errors += seg_errors
    warnings += seg_warnings

    warnings += check_custom_format_warning(profile)

    fatal_errors += check_particle_config(profile)
    fatal_errors += check_smoke_test_report_path(profile)

    durable_root = Path(profile["project"]["durable_root"]).expanduser()
    fatal_errors += check_resumed_contract_versions(durable_root)

    for warning in warnings:
        print(f"WARNING: {warning}")
    for err in fatal_errors:
        print(f"ERROR: {err}", file=sys.stderr)

    if fatal_errors:
        sys.exit(1)

    suffix = " (see warnings above)" if warnings else ""
    print(f"{profile_path}: OK -- Step 0 validation passed{suffix}")
    sys.exit(0)


if __name__ == "__main__":
    main()
