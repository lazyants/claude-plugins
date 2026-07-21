#!/usr/bin/env python3
"""canon_senses.py -- the homonym-split sidecar's shared normalizer + the
ONE runtime-validating loader for canon_senses.json.

canon_senses.json is a sibling of canon.json (default path
``{durable_root}/canon_senses.json``): it records a source_form that
resolves to TWO OR MORE distinct target senses within one project (a split
is >=2 senses). Its shape is fully specified by
``canon-senses.schema.json`` -- read that file's own description before
changing anything here.

This module is a **project-dependency LEAF w.r.t. every OTHER first-party
module that could cycle back to it**: it imports nothing from
``canon_adjudication_audit``, ``canon_validate``, or
``glossary_batch_plan``, so there is no import cycle even though the
audit script itself needs ``load_senses``. ``normalize_form`` was
relocated here from ``canon_adjudication_audit.py`` for the same reason --
leaving it in the audit module would force every OTHER consumer
(``canon_validate.py``'s recollapse guard, ``glossary_batch_plan.py``'s
split-form exclusion) to import the audit module just for one helper,
which would make the audit module a transitive dependency of the two
plugin-bundle members (``cache_key.py``'s ``PLUGIN_BUNDLE_MEMBERS``) that
import them -- silently invisible to the bundle hash.

**#243 exception, LAZY.** ``fold_collision_map()`` (below) needs
``bootstrap_names.fold_match_key`` -- but importing it at MODULE level, the
way every other first-party dependency in this plugin is guarded, would
break the leaf property above for every context that imports
``normalize_form``/``load_senses`` without ``bootstrap_names.py``
installed alongside (this shipped once and broke 81 tests in
``tests/merged_disk_verify.test.py``, which import this module in
isolation). So the import happens INSIDE ``fold_collision_map()`` itself
-- the only place that needs it -- materializing the dependency only when
that one function is actually called, never merely by importing this
module. A missing ``bootstrap_names.py`` at call time RAISES (never
``sys.exit()`` -- a library function must not kill its host process; that
pattern stays reserved for the module-level ``jsonschema`` guard below, a
genuine unconditional dependency of every consumer). This does not reopen
an import cycle -- ``bootstrap_names.py`` itself imports no first-party
module, so it is a leaf in the same sense this module is -- and it does
not need a new freshness-closure entry: ``bootstrap_names.py`` is
already, independently, a member of every closure ``canon_senses.py``
itself is a member of (``suspicion_scan.PRODUCER_CODE_CLOSURE``,
``skeptic_setup``'s closure union), so a change to ``fold_match_key``
already invalidates them both today. ``bootstrap_names.py`` was itself
REJECTED as ``fold_collision_map()``'s home for the opposite reason: it is
a ``cache_key.DERIVATION_BUNDLE_MEMBERS`` member, and putting a
worklist/skeptic-facing helper there would move the derivation-bundle
hash on a code change that has nothing to do with derivation (the #193
dead-end). This module is a ``PLUGIN_BUNDLE_MEMBERS`` member instead
(already paid for), and is not a derivation-bundle member -- the cheaper,
correct home.

"Leaf" means dependency-DIRECTION, not stdlib-only: this module DOES
import ``jsonschema`` (``requirements.txt`` pins ``jsonschema>=4.26.0``)
because ``load_senses``'s schema validation genuinely needs a real
``Draft202012Validator``. Per this plugin's dependency-preflight
discipline (``references/gotchas.md``'s "Every script needing
jsonschema/PyYAML/beautifulsoup4/lxml must wrap its import in a try/except
with an actionable pip install -r requirements.txt message" -- mirrors
``canon_validate.py``'s own module-level try/except exactly), the import
is wrapped and exits with an actionable message rather than raising a raw
``ImportError``.

Every consumer imports exactly two names from here:

    from canon_senses import normalize_form, load_senses

(``canon_adjudication_audit.py``'s ``collapsed_split``/``homonym_split``
identity, ``canon_validate.py``'s recollapse guard, and
``glossary_batch_plan.py``'s split-form exclusion all compare source_forms
via ``normalize_form`` and load the sidecar via ``load_senses`` -- never a
private partial reader and never a second normalizer implementation.)
``is_split`` is provided as a convenience predicate so no consumer has to
re-derive "len(senses) >= 2, compared via normalize_form" itself.
"""
import json
import os
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Union

try:
    import jsonschema
except ImportError as e:
    sys.stderr.write(
        "canon_senses.py requires the 'jsonschema' package (>=4.26.0) to "
        "validate canon_senses.json against canon-senses.schema.json. "
        "Install with:\n\n"
        "    pip install -r requirements.txt\n\n"
        "(or directly: pip install 'jsonschema>=4.26.0')\n\n"
        f"(import error: {e})\n"
    )
    sys.exit(1)

# Self-anchored: this script always lives at
# ${durable_root}/scripts/canon_senses.py, so parents[1] is the durable
# root -- same convention as canon_validate.py, never cwd-relative. See
# references/ledger-and-resumability.md's "Script self-anchoring"
# invariant.
_SCRIPT_FILE = Path(__file__).resolve()
SCRIPTS_DIR = _SCRIPT_FILE.parent
DURABLE_ROOT = _SCRIPT_FILE.parents[1]
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
DEFAULT_SCHEMA_PATH = SCHEMAS_DIR / "canon-senses.schema.json"

# A real canon_senses.json is fixed-depth and shallow (root object ->
# entries_by_source_form -> entry -> senses[] -> sense -> evidence, i.e.
# depth 6 -- see canon-senses.schema.json). 100 is a generous ceiling well
# above that real shape, used by `_measure_nesting_depth`'s preflight
# below to reject a pathologically deep document BEFORE it ever reaches
# jsonschema or repr() -- both of which recurse on the document's actual
# nesting, not the schema's, so a hostile deep `doc` can blow the stack
# there even after `json.loads` itself already succeeded on it (the
# trigger point moves with the interpreter's incidental stack depth, so a
# handful of `except RecursionError` clauses is not, by itself, a
# deterministic fix -- see those clauses' own comments for why they stay
# as a backstop regardless).
MAX_NESTING_DEPTH = 100

# Deliberately NOT defined here: DEFAULT_SENSES_PATH lives in
# canon_validate.py, self-anchored as the sibling of DEFAULT_CANON_PATH --
# each consumer computes its own copy the same way it already computes
# other durable-root-relative defaults.


class CanonSensesLoadError(Exception):
    """Raised for any failure that should block a caller of `load_senses`
    -- a non-regular sidecar path, a schema-validation failure, or a
    procedural reject JSON Schema cannot express (duplicate sense_id
    within an entry, a non-NFC entries_by_source_form key, two keys
    colliding under normalize_form()). `offending`, when not None, is
    folded into the failure payload verbatim so a caller never has to
    re-derive it from a bare error string.
    """

    def __init__(self, message, offending=None):
        super().__init__(message)
        self.offending = offending


def normalize_form(s: str) -> str:
    """NFC-normalize, casefold, and collapse-and-strip internal whitespace
    -- the ONE normalizer every grouping/matching key across the
    homonym-split feature is computed from (relocated here from
    canon_adjudication_audit.py:296, which now imports it from this
    module instead of defining its own copy). Display fields always keep
    the original string; only grouping/hashing/comparison ever sees the
    normalized form."""
    return " ".join(unicodedata.normalize("NFC", s).casefold().split())


@dataclass(frozen=True)
class SensesResult:
    """The return type of `load_senses`.

    `entries_by_source_form` is the raw, already schema-and-procedurally
    validated `entries_by_source_form` mapping from canon_senses.json,
    keyed by each entry's own literal (NFC, display-form) source_form
    string -- exactly as canon-senses.schema.json shapes it. `is_empty` is
    True iff the sidecar was genuinely absent (and the caller passed
    `allow_absent=True`) OR the loaded document is schema-valid with
    `entries_by_source_form == {}`. Consumers branch on `.is_empty`
    rather than `bool(.entries_by_source_form)` to keep that intent
    explicit at each call site.

    `normalized_index` is an OPTIONAL precomputed `normalize_form(key) ->
    entry` map, built once by `load_senses` (via `_build_normalized_index`)
    so `is_split`/`canon_validate.py`'s `_matching_senses_entry` can do an
    O(1) dict lookup instead of re-normalizing every key on every call --
    at 10k candidates x 10k split entries the old linear rescan was
    ~100M normalize_form calls. Defaults to `None` so the two test suites
    that construct a `SensesResult` directly (never through `load_senses`)
    keep working unchanged: both predicates fall back to the original
    linear scan whenever `normalized_index is None`, just without the
    speedup."""

    is_empty: bool
    entries_by_source_form: dict
    normalized_index: Optional[dict] = None


def _build_normalized_index(entries_by_source_form: dict) -> dict:
    """Builds the `normalize_form(key) -> entry` map `load_senses` attaches
    to `SensesResult.normalized_index`.

    Uses `setdefault` -- never plain assignment -- so the FIRST key (in
    `entries_by_source_form`'s iteration order) whose normalized form
    matches wins, exactly mirroring the linear scan's own first-match
    behavior; a later colliding key must never overwrite an earlier one.
    In practice `load_senses`'s own `_procedural_checks` already refuses
    to load a document with two normalize_form-colliding keys, so this
    only matters for a `SensesResult` some other caller builds by hand
    (as a couple of tests do) -- the setdefault keeps this helper correct
    either way."""
    index: dict = {}
    for key, entry in entries_by_source_form.items():
        index.setdefault(normalize_form(key), entry)
    return index


def is_split(result: SensesResult, source_form: str) -> bool:
    """True iff `source_form` -- compared via `normalize_form`, never a
    raw key lookup -- has an adjudicated split entry in `result`: an
    entry whose `senses` has `len(senses) >= 2` (a split is >=2 senses,
    never 1; `load_senses`'s schema validation already refuses to load a
    1-sense record at all, so this length check is really just reading
    that invariant back). Consumers (the recollapse guard, the planner's
    split-form exclusion, the audit's `collapsed_split` reconciliation)
    call this instead of re-deriving the comparison themselves, so the
    predicate can't drift between call sites.

    Uses `result.normalized_index` for an O(1) lookup when `load_senses`
    built one; falls back to the original O(n) linear scan (normalizing
    every key on every call) only for a `SensesResult` constructed
    directly without an index -- both paths must return identically."""
    target = normalize_form(source_form)
    if result.normalized_index is not None:
        entry = result.normalized_index.get(target)
        return entry is not None and len(entry.get("senses", [])) >= 2
    for key, entry in result.entries_by_source_form.items():
        if normalize_form(key) == target:
            return len(entry.get("senses", [])) >= 2
    return False


# ---------------------------------------------------------------------------
# fold_collision_map() -- the shared #238/#241 fold-key collision detector
# (#243). Home chosen on freshness-closure cost, not taste -- see this
# module's own docstring, "#243 exception, deliberate".
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FoldCollisionMap:
    """The return type of `fold_collision_map()`.

    `groups` is `bootstrap_names.fold_match_key(form) -> (raw forms sharing
    that key,)`, insertion-ordered (a form's position is its first
    occurrence in the `source_forms` `fold_collision_map()` was built from;
    a repeated identical raw form is deduplicated within its group, never
    counted twice). A key whose group has exactly one member never
    collided; `len(group) >= 2` is what "collides" means throughout this
    plugin (occurrence_targets.py's `_colliding_source_forms`,
    bootstrap_names.py's `_warn_inventory_match_key_collisions`) -- this
    dataclass generalizes that same many-to-one check into one reusable,
    importable result any consumer can hold onto and query repeatedly,
    instead of re-deriving its own `defaultdict(list)` pass.

    `colliding` is the flattened `frozenset` of every raw form belonging to
    a group of size >= 2 -- exactly `is_colliding()`'s backing set, exposed
    directly for a caller that wants to test membership against many forms
    at once (e.g. a set intersection) rather than one call per form.
    """

    groups: dict
    colliding: frozenset

    def is_colliding(self, form: str) -> bool:
        """True iff `form` -- compared by RAW identity, never re-folded --
        is a member of a fold-key group of size >= 2. A `form` that was
        never part of the `source_forms` this map was built from is never
        `is_colliding` (there is nothing to compare it against); this is
        why every caller builds its `FoldCollisionMap` over the full
        COMPETITOR universe (this module's own docstring on that
        distinction), not just its own local, eligible-for-output
        projection -- a form absent from the competitor universe can never
        be detected as colliding here, by construction."""
        return form in self.colliding


def fold_collision_map(source_forms: Iterable[str]) -> FoldCollisionMap:
    """Groups `source_forms` by `bootstrap_names.fold_match_key` and reports
    every many-to-one (size >= 2) group as `.colliding` -- the single
    shared collision-detection ALGORITHM every #243 fold site (`occ_index.
    py`'s `index_manifest()`, `evidence_verify.py`'s
    `_group_production_spans_by_name()`, and, upstream of both, whichever
    caller assembles the COMPETITOR universe those two consult) uses,
    rather than each re-implementing its own copy of
    `occurrence_targets.py`'s pre-existing `_colliding_source_forms()`
    pattern.

    Two distinct concepts a caller must not conflate (do not skip this if
    you are about to call this function):

    - **Competitors** -- who PARTICIPATES in collision detection. This is
      the union of every `canon.json` `entries` key AND every
      `canon_senses.json` `entries_by_source_form` key (split-only forms
      INCLUDED -- a split-only form is deliberately excluded from
      `canon.json` itself, `glossary_batch_plan.py`'s split-form
      exclusion, but it still occupies a real fold key and must still
      poison an ambiguous match). The competitor set is the SAME for
      every consumer in one audit run -- build `fold_collision_map()` once
      over it, not once per consumer.
    - **Eligible-for-output** -- who actually gets an index record / a
      worklist row / a verified-evidence credit. This is each consumer's
      own local, already-scoped projection (`index_manifest()`'s own
      `source_forms` argument, `build_worklist()`'s `scope_in`) -- NEVER
      the full competitor set. A split-only form is a competitor (it can
      still poison another form's match) but is never itself eligible for
      output.

    Passing a consumer's local projection instead of the full competitor
    set here would silently miss a real collision whenever the two
    colliding forms land in DIFFERENT local projections (e.g. one form
    filtered out of scope, or a split-only form that never appears in
    `canon.json` at all) -- exactly the class of bug this plugin's #243
    fail-closed collision semantics exist to prevent (see
    `occurrence_targets.py`'s own module docstring, "The fold NEWLY
    introduces...", for the reference case this generalizes).

    LAZY import (C1-AMENDMENT): `bootstrap_names.fold_match_key` is
    imported HERE, not at module level -- see this module's own docstring,
    "#243 exception, LAZY", for why. RAISES `RuntimeError` (never
    `sys.exit()`) if `bootstrap_names.py` is not installed alongside this
    module -- a library function must not kill its caller's process.
    """
    try:
        from bootstrap_names import fold_match_key
    except ImportError as exc:
        raise RuntimeError(
            f"canon_senses.fold_collision_map(): cannot import bootstrap_names.py from "
            f"{SCRIPTS_DIR} ({exc}). bootstrap_names.py must be installed alongside "
            "canon_senses.py under ${durable_root}/scripts/ -- it supplies "
            "fold_match_key(), the #238/#241 Hebrew mark/connector MATCH KEY this "
            "function groups source forms by. Re-run Step 0a, or verify the plugin "
            "install is not corrupted."
        ) from exc

    order: dict = {}
    for form in source_forms:
        key = fold_match_key(form)
        members = order.setdefault(key, [])
        if form not in members:
            members.append(form)
    groups = {key: tuple(members) for key, members in order.items()}
    colliding = frozenset(
        form for members in groups.values() if len(members) >= 2 for form in members
    )
    return FoldCollisionMap(groups=groups, colliding=colliding)


def _path_state(path: Path) -> str:
    """Classifies `path` as "absent" / "regular" / "irregular".

    Presence is detected via `os.path.lexists` (never `Path.exists`,
    which follows symlinks and would misreport a dangling symlink as
    absent) -- mirrors canon_adjudication_audit.py's own lexists discipline.
    A present path only counts as "regular" if `Path.is_file()` is also
    true (which DOES follow symlinks, so a symlink to a regular file is
    "regular" while a symlink to a directory, a dangling symlink, or a
    device node is "irregular") -- mirrors glossary_batch_plan.py's
    `is_file()` gate.
    """
    if not os.path.lexists(path):
        return "absent"
    if path.is_file():
        return "regular"
    return "irregular"


def _measure_nesting_depth(obj: Any) -> int:
    """Measures the deepest object/array nesting level in `obj` -- an
    empty or scalar-only document is depth 1, and depth increases by 1
    for each additional level of dict/list nesting. Deliberately an
    EXPLICIT STACK, never a recursive helper: this function exists to
    catch a pathologically deep document BEFORE anything else touches it,
    so it cannot itself be the thing that blows the stack."""
    stack = [(obj, 1)]
    deepest = 1
    while stack:
        current, depth = stack.pop()
        if isinstance(current, dict):
            deepest = max(deepest, depth)
            stack.extend((v, depth + 1) for v in current.values())
        elif isinstance(current, list):
            deepest = max(deepest, depth)
            stack.extend((v, depth + 1) for v in current)
    return deepest


def _reject_unencodable_strings(doc: Any, source_path: Path) -> None:
    """Iteratively (explicit stack, no recursion -- same discipline as
    `_measure_nesting_depth`) walks every string in `doc`, both
    `entries_by_source_form` KEYS and every value at any depth, and
    rejects any that cannot round-trip through UTF-8.

    `json.loads` happily accepts a JSON-escaped LONE SURROGATE (e.g. the
    literal 6-character escape sequence for U+D800) and hands back a
    valid Python `str` containing that unpaired surrogate codepoint --
    which passes schema validation and the procedural checks just fine,
    then blows up the FIRST time any consumer encodes it (e.g.
    canon_adjudication_audit.py's identity-key construction calls
    `.encode("utf-8")` on a loaded source_form). That must be caught HERE,
    at load time, as the documented CanonSensesLoadError -- not left to
    crash some later consumer with a raw, undocumented UnicodeEncodeError.

    `loc` tracks the ancestor path as a tuple of already-validated
    segments (never the currently-failing one), so building the "where"
    string for the error message is always safe -- it can't itself
    contain an unencodable segment. The offending key/value is reported
    via `repr()`, which never requires encoding."""
    def _check(kind: str, s: str, loc: tuple) -> None:
        try:
            s.encode("utf-8")
        except UnicodeEncodeError as err:
            where = "/".join(str(p) for p in loc) or "<root>"
            raise CanonSensesLoadError(
                f"canon_senses.json at {source_path}: {kind} {s!r} "
                f"at '{where}' is not valid UTF-8: {err}"
            )

    stack: list[tuple[Any, tuple[Any, ...]]] = [(doc, ())]
    while stack:
        current, loc = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if isinstance(key, str):
                    _check("key", key, loc)
                stack.append((value, loc + (key,)))
        elif isinstance(current, list):
            for index, value in enumerate(current):
                stack.append((value, loc + (index,)))
        elif isinstance(current, str):
            _check("value", current, loc)


def _parse_utf8_json_with_depth_guard(content: bytes, describe: str) -> Any:
    """Pure decode/parse/depth-preflight core of
    ``_read_utf8_json_with_depth_guard`` -- operates on ALREADY-READ bytes,
    no I/O of any kind, so there is no OSError branch here: a caller with
    bytes already in hand has nothing left that can fail to open/read.
    ``describe`` is the human label opening every ``CanonSensesLoadError``
    this raises (e.g. ``"canon_senses.json at <path>"``), so callers get
    caller-specific messages without re-deriving them.

    Codex round 5: split out of ``_read_utf8_json_with_depth_guard`` so a
    caller that already captured a ``(state, content)`` snapshot (e.g. via
    ``suspicion_scan.read_frozen_input_snapshot()``) for a trust decision
    -- a producer/skeptic digest, an H1 stamp -- can parse THOSE SAME
    bytes instead of re-reading the path a second time for a second,
    potentially-disagreeing decision. See ``load_senses_from_snapshot``'s
    own docstring.

    Layers preserved verbatim from the pre-extraction function (do not
    merge these into a single try/except -- see the layered-exception
    design note in load_senses's docstring):

      1. ``.decode("utf-8")`` raises the ``ValueError``-subclass
         ``UnicodeDecodeError`` (bytes aren't UTF-8) -- caught so the
         decode failure never escapes as a raw traceback past
         load_senses's ``CanonSensesLoadError``-only contract.
      2. ``json.loads`` raises ``JSONDecodeError`` or, on a pathologically
         deep-nested document, ``RecursionError`` before ``JSONDecodeError``
         can even fire. The RecursionError branch stays as a BACKSTOP even
         though the depth preflight just below is meant to make it
         unreachable in practice: ``json.loads`` itself is where the
         pathological depth first gets exercised.
      3. Deterministic depth kill via the iterative
         ``_measure_nesting_depth`` -- the primary defense for the whole
         RecursionError class: reject BEFORE ``doc`` ever reaches
         jsonschema's recursive validator or an error path that would
         ``repr()`` it (see ``MAX_NESTING_DEPTH``'s own comment for why
         the RecursionError trigger point is interpreter-dependent).
    """
    try:
        raw = content.decode("utf-8")
    except UnicodeDecodeError as e:
        raise CanonSensesLoadError(f"{describe} is not valid UTF-8: {e}")
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CanonSensesLoadError(f"{describe} is not valid JSON: {e}")
    except RecursionError as e:
        raise CanonSensesLoadError(f"{describe} is nested too deeply to parse: {e}")
    depth = _measure_nesting_depth(doc)
    if depth > MAX_NESTING_DEPTH:
        raise CanonSensesLoadError(
            f"{describe} is nested {depth} levels deep, "
            f"exceeding the maximum of {MAX_NESTING_DEPTH}"
        )
    return doc


def _read_utf8_json_with_depth_guard(path: Path, describe: str) -> Any:
    """Read/parse/depth-preflight body of ``_load_schema_document`` (its
    only caller). Thin path-reading wrapper around
    ``_parse_utf8_json_with_depth_guard`` (codex round 5) -- for callers
    that want a fresh read, never for a caller that already has a
    captured snapshot to parse instead (``_parse_json_from_bytes`` below
    is that byte-based sibling, for ``canon_senses.json`` specifically --
    ``_load_schema_document`` never has a captured snapshot to reuse,
    since the schema file itself is never part of any H1/digest
    snapshot). ``describe`` is the human label opening every
    ``CanonSensesLoadError`` this raises -- always ``"schema at <path>"``
    from this function's own sole caller today, but kept as a caller
    argument rather than hardcoded since the pure core below
    (``_parse_utf8_json_with_depth_guard``) shares the same parameter for
    its OWN callers' different labels.

    ``read_bytes`` raises ``OSError`` (can't open) -- caught here, outside
    the pure core, since a caller with bytes already in hand (the core's
    other entry point) has no read left to fail.
    """
    try:
        content = path.read_bytes()
    except OSError as e:
        raise CanonSensesLoadError(f"could not read {describe}: {e}")
    return _parse_utf8_json_with_depth_guard(content, describe)


def _parse_json_from_bytes(content: bytes, senses_path: Path) -> Any:
    """Parses ``canon_senses.json``'s own ALREADY-CAPTURED snapshot
    (``content``) instead of reading ``senses_path`` itself -- the
    ``_reject_unencodable_strings`` counterpart to
    ``_read_utf8_json_with_depth_guard``'s own path-based read/parse/
    depth-preflight, called by ``load_senses_from_snapshot`` below.
    ``senses_path`` is used for error-message labeling only, matching the
    ``describe`` messages ``_parse_utf8_json_with_depth_guard`` itself
    already produces from it."""
    doc = _parse_utf8_json_with_depth_guard(content, f"canon_senses.json at {senses_path}")
    _reject_unencodable_strings(doc, senses_path)
    return doc


def _load_schema_document(schema_path: Path) -> dict:
    # Symmetric hardening -- canon-senses.schema.json itself is a trusted,
    # shipped file, but a corrupted copy on disk gets the same deterministic
    # depth preflight the sidecar path gets, rather than relying on the
    # RecursionError backstop alone.
    return _read_utf8_json_with_depth_guard(schema_path, f"schema at {schema_path}")


def _schema_validate(doc: Any, schema_path: Path, senses_path: Path) -> None:
    """Validates `doc` against the schema at `schema_path`, raising
    CanonSensesLoadError on the FIRST error (sorted by instance path for
    determinism) if any. canon-senses.schema.json is self-contained (no
    $ref to another schema file), so a plain Draft202012Validator needs
    no registry -- unlike canon_validate.py's cross-file canon-*.schema.json
    set.

    `doc` is already bounded to `MAX_NESTING_DEPTH` by
    `_parse_utf8_json_with_depth_guard`'s depth preflight -- `doc`'s only
    producer is `_parse_json_from_bytes` (this module's sole caller of
    `_schema_validate`, `load_senses_from_snapshot`, parses the sidecar
    that way regardless of whether the caller arrived via the path-based
    `load_senses` wrapper or `load_senses_from_snapshot` directly) --
    before this is ever called, so the RecursionError
    guard below should be unreachable in practice -- it stays as a
    backstop, not the primary defense. It exists because a top-level
    `type` mismatch (`doc` is a list, not the required object) fails
    without the validator ever descending into it, yet jsonschema's own
    error message formats `{instance!r}`, and repr() of a deeply nested
    list recurses once per level -- so even a SHALLOW schema, given a
    deep enough `doc`, could have blown the stack building that one error
    message before the preflight existed."""
    schema = _load_schema_document(schema_path)
    validator = jsonschema.Draft202012Validator(schema)
    try:
        errors = sorted(validator.iter_errors(doc), key=lambda e: [str(p) for p in e.path])
    except RecursionError as e:
        raise CanonSensesLoadError(
            f"canon_senses.json at {senses_path} is nested too deeply to validate: {e}"
        )
    if errors:
        first = errors[0]
        loc = "/".join(str(p) for p in first.path) or "<root>"
        raise CanonSensesLoadError(
            f"canon_senses.json at {senses_path} failed schema validation "
            f"at '{loc}': {first.message}"
        )


def _procedural_checks(doc: dict, senses_path: Path) -> None:
    """Rejects the three things canon-senses.schema.json cannot express
    (see its own top-level description): a duplicate sense_id within one
    entry, a non-NFC entries_by_source_form key, and two keys colliding
    under normalize_form()."""
    entries = doc.get("entries_by_source_form", {})
    seen_normalized: dict = {}
    for key, entry in entries.items():
        if unicodedata.normalize("NFC", key) != key:
            raise CanonSensesLoadError(
                f"canon_senses.json at {senses_path}: entries_by_source_form "
                f"key {key!r} is not NFC-normalized",
                offending=key,
            )
        normalized = normalize_form(key)
        if normalized in seen_normalized:
            raise CanonSensesLoadError(
                f"canon_senses.json at {senses_path}: keys "
                f"{seen_normalized[normalized]!r} and {key!r} collide under "
                f"normalize_form() ({normalized!r})",
                offending=[seen_normalized[normalized], key],
            )
        seen_normalized[normalized] = key

        seen_sense_ids: set = set()
        for sense in entry.get("senses", []):
            sense_id = sense.get("sense_id")
            if sense_id in seen_sense_ids:
                raise CanonSensesLoadError(
                    f"canon_senses.json at {senses_path}: duplicate sense_id "
                    f"{sense_id!r} within entry {key!r}",
                    offending=sense_id,
                )
            seen_sense_ids.add(sense_id)


def load_senses_from_snapshot(
    path: Union[str, Path],
    state: str,
    content: bytes,
    *,
    allow_absent: bool,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
) -> SensesResult:
    """Byte-based CORE of the runtime-validating loader -- parses/validates
    an ALREADY-CAPTURED ``(state, content)`` snapshot (e.g. from
    ``suspicion_scan.read_frozen_input_snapshot()``) instead of deriving
    one via a fresh read of ``path``. ``path`` is used for error-message
    labeling ONLY, never for I/O.

    Codex round 5: a caller making a trust decision off bytes it already
    holds -- a producer/skeptic input digest, an H1 tamper stamp -- must
    parse THOSE SAME bytes here, never re-read ``path`` a second time for
    a second, potentially-disagreeing decision (the approved snapshot
    silently ceasing to be the consumed snapshot). ``load_senses()`` below
    is the thin, path-reading wrapper for callers with no snapshot of
    their own to hand in -- as of round 5 that's every ordinary caller
    (``canon_adjudication_audit.py``, ``canon_validate.py``,
    ``glossary_batch_plan.py``, ``assemble.py``, ``validate_backlinks.py``):
    none of them makes a separate trust decision off independently-read
    bytes of the same file, so a single fresh read is genuinely correct
    for all five. ``suspicion_scan.py``'s ``main()`` and
    ``skeptic_ready.py``'s frozen-input-check-then-resolve-competitors
    path are the two exceptions that call this function directly.

    1. Path-state policy: `allow_absent=True` tolerates ONLY a genuinely
       absent path (an implicit default that was never written yet). An
       explicit path the caller expected to exist (`allow_absent=False`)
       is a BLOCK when absent. ANY non-regular path -- a directory, a
       dangling symlink, a device node -- is a BLOCK regardless of
       `allow_absent` (mirrors glossary_batch_plan.py:187 +
       canon_adjudication_audit.py:739).
    2. When present, schema-validates against `schema_path` FIRST, before
       any emptiness decision -- a schema failure is always BLOCKING,
       never silently "no senses". A raw `{}` fails here (missing
       required `schema_version`/`entries_by_source_form`); there is no
       pre-validation special-case for it.
    3. Applies the procedural rejects the schema cannot express (see
       `_procedural_checks`).
    4. Returns a `SensesResult`. `is_empty` is True iff the path was
       genuinely absent (case 1) or the schema-valid document's
       `entries_by_source_form` is `{}`.
    """
    senses_path = Path(path)

    if state == "irregular":
        raise CanonSensesLoadError(
            f"canon_senses.json path exists but is not a regular file: {senses_path}"
        )
    if state == "absent":
        if allow_absent:
            return SensesResult(
                is_empty=True, entries_by_source_form={}, normalized_index={}
            )
        raise CanonSensesLoadError(f"canon_senses.json not found at {senses_path}")

    doc = _parse_json_from_bytes(content, senses_path)
    _schema_validate(doc, Path(schema_path), senses_path)
    _procedural_checks(doc, senses_path)

    entries = doc.get("entries_by_source_form", {})
    return SensesResult(
        is_empty=(len(entries) == 0),
        entries_by_source_form=entries,
        normalized_index=_build_normalized_index(entries),
    )


def load_senses(
    path: Union[str, Path],
    *,
    allow_absent: bool,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
) -> SensesResult:
    """THE loader for callers with no already-captured snapshot of their
    own: classifies `path`, reads it fresh, and delegates to
    `load_senses_from_snapshot` for parsing/validation -- codex round 5
    split this into a thin path-reading wrapper around that byte-based
    core; see its docstring for which of the two a given caller should
    use and why. Every existing caller of THIS function keeps its exact
    prior behavior unchanged (same messages, same fresh-read semantics)."""
    senses_path = Path(path)
    state = _path_state(senses_path)
    try:
        content = senses_path.read_bytes() if state == "regular" else b""
    except OSError as e:
        raise CanonSensesLoadError(f"could not read canon_senses.json at {senses_path}: {e}")
    return load_senses_from_snapshot(
        senses_path, state, content, allow_absent=allow_absent, schema_path=schema_path
    )
