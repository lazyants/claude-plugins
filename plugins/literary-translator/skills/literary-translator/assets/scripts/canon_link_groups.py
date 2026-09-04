#!/usr/bin/env python3
"""canon_link_groups.py -- the ONE runtime-validating loader for the
one-entity link-routing sidecar, canon_link_groups.json (#588).

canon_link_groups.json is a sibling of canon.json (default path
``{durable_root}/canon_link_groups.json``): it records that N canon
entries -- N distinct ``source_form`` spellings of ONE referent -- may
share a single inline wikilink target instead of being de-linked as a
homonym collision. Its shape is fully specified by
``canon-link-groups.schema.json`` -- read that file's own description
before changing anything here.

## Why this file exists

``render_obsidian.build_entity_index`` de-links a ``canonical_target_form``
owned by >=2 canon entries on every real obsidian render (#206/#207): a
misattributed inline link (a click landing on the WRONG entity's note) is
strictly worse than a missing one. That rule cannot tell two spellings of
one man apart from two different men, and in a pointed-script corpus the
first case is the NORMAL one -- the same name with and without maqaf, or
with different niqqud, is several canon entries and one person. So the
book's most frequently named figures lose every inline link they have
(#588 measured 1373 unlinked occurrences against 537 emitted links in one
delivered vault, with every gate green).

A group is how an ALREADY-MADE identity call re-links them. Four things it
deliberately does NOT do:

1. It changes ONLY targets that would otherwise be de-linked. A target with
   exactly one owner is untouched, group or no group.
2. It never widens the matcher. The alternation is built from the same
   ``canonical_target_form`` strings either way, so no string becomes newly
   matchable and no prose is newly rewritten (#587's demonym hazard is out
   of reach by construction).
3. It is NOT an entity/coreference layer. ``canon.json`` stays a 1:1 name
   dictionary and every member keeps its own entity note and its own
   frontmatter. What it does NOT promise, and did claim before #497, is that
   every member also keeps its own ``## Mentions`` appendix. Where a group's
   members collide on a ``#238/#241`` fold key -- the normal case in a
   pointed-script corpus, and the only case where the appendix was ever at
   stake -- the group's occurrences are credited to the group's PRIMARY, so
   the primary's appendix is the collapse-free index for the referent and the
   other members' notes carry none. Before #497 NONE of them did: a fold
   collision withheld every member's occurrences outright
   (``occurrence_targets.py``, "The third resolution route"). Outside a fold
   collision nothing changes -- each form indexes its own occurrences as it
   always has.
4. It never makes the identity call itself. THE IRON RULE: scripts surface
   candidates and enforce schemas, they never decide "are these two forms
   the same entity". This file is where a call made upstream (an operator,
   or a codex/LLM adjudication pass) is RECORDED -- which is why every group
   must carry a non-blank ``note``.

## Why a sidecar and not a canon field

``cache_key.compute_used_terms_hash`` hashes the WHOLE referenced canon
ENTRY object, so adding any field to ``canon['entries'][name]`` RE-STALES
every converged segment that references that name -- which then costs bounded
re-review under an admissible ``--from-converged`` claim, or an outright
re-translation under ``--allow-retranslate-converged``. Putting the grouping
in a sibling file instead keeps it outside all 15 cache-key fields, so a finished
book can adopt a group with NOTHING re-staled at all -- which is the whole point:
it is what makes the cheap fix actually cheap. (The code change that READS
the sidecar is a separate matter; see the CHANGELOG's own migration note for
what re-renders.)

## Entry point

    load_link_groups(path, entries, allow_absent=True) -> dict

Returns ``{member_source_form: primary_source_form}`` -- the flat map
``render_obsidian.build_entity_index`` consumes -- or ``{}`` for an absent
file (with ``allow_absent=True``) or a schema-valid empty ``groups: []``.

Keys and values are the LITERAL, byte-exact ``canon['entries']`` keys, never
folded or NFC-renormalized: ``build_entity_index`` looks the owner's own raw
``source_form`` up in this map, so any normalization here would silently
miss. A member that is not a key of ``entries`` is a hard load error rather
than a tolerated no-op -- exactly the failure a silent miss would hide.

A DUPLICATE object key is a rejection too, not a last-one-wins merge:
``json.loads`` would collapse ``{"primary": "A", "primary": "B"}`` to ``B``
before jsonschema ever sees it, and the strict schema cannot express a
constraint on a key that no longer exists twice -- see
``_reject_duplicate_keys``.

Raises ``CanonLinkGroupsLoadError`` for every rejection (never
``sys.exit()`` -- a library function must not kill its host process; that
pattern stays reserved for the module-level dependency guard below, a
genuine unconditional dependency of every consumer).

This module is a dependency LEAF: it imports no other first-party module, so
``assemble.py``, ``validate_backlinks.py`` and a test can import it in
isolation. It is deliberately a member of NO bundle tuple
(``cache_key.PLUGIN_BUNDLE_MEMBERS`` / ``DERIVATION_BUNDLE_MEMBERS`` /
``scaffold_setup.ORCHESTRATION_BUNDLE_MEMBERS``) -- every one of its
importers is outside those tuples too, so it introduces no
transitive-import invisibility. Registering it would move a hash and
re-translate books for a link-routing change that cannot affect a single
translated word.
"""

import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError as e:
    sys.stderr.write(
        "canon_link_groups.py requires the 'jsonschema' package (>=4.26.0) "
        "to validate canon_link_groups.json against "
        "canon-link-groups.schema.json. Install with:\n\n"
        "    pip install -r requirements.txt\n\n"
        "(or directly: pip install 'jsonschema>=4.26.0')\n\n"
        f"(import error: {e})\n"
    )
    sys.exit(1)

# Self-anchored: this script always lives at
# ${durable_root}/scripts/canon_link_groups.py, so parents[1] is the durable
# root -- same convention as canon_senses.py, never cwd-relative.
_SCRIPT_FILE = Path(__file__).resolve()
SCRIPTS_DIR = _SCRIPT_FILE.parent
DURABLE_ROOT = _SCRIPT_FILE.parents[1]
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
DEFAULT_SCHEMA_PATH = SCHEMAS_DIR / "canon-link-groups.schema.json"
DEFAULT_LINK_GROUPS_PATH = DURABLE_ROOT / "canon_link_groups.json"

# A real canon_link_groups.json is fixed-depth and shallow (root object ->
# groups[] -> group -> members[] -> string, i.e. depth 4). 20 is a generous
# ceiling used by the ITERATIVE preflight below to reject a pathologically
# deep document BEFORE it reaches jsonschema, whose error messages format
# `{instance!r}` and therefore recurse on the document's own nesting.
MAX_NESTING_DEPTH = 20


class CanonLinkGroupsLoadError(Exception):
    """Raised for every rejection: an unreadable/non-regular file, invalid
    UTF-8 or JSON, a too-deep document, a schema violation, or one of the
    three procedural rejects the schema cannot express (a primary outside
    its own members, a member that is not a canon entries{} key, one
    source_form in two groups). `offending`, when not None, is the exact
    value at fault, so a caller never has to re-derive it from the message.
    """

    def __init__(self, message, offending=None):
        super().__init__(message)
        self.offending = offending


def _exceeds_depth(obj, limit):
    """True if `obj` nests deeper than `limit`. ITERATIVE on purpose: a
    recursive probe would itself blow the stack on the very document it
    exists to reject."""
    stack = [(obj, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > limit:
            return True
        if isinstance(node, dict):
            for value in node.values():
                stack.append((value, depth + 1))
        elif isinstance(node, list):
            for value in node:
                stack.append((value, depth + 1))
    return False


class _DuplicateKey(ValueError):
    """Internal: raised by `_reject_duplicate_keys` and re-labelled by
    `_read_json`. A ValueError so a stray escape still lands in the same
    `except ValueError` net as a malformed document."""


def _reject_duplicate_keys(pairs):
    """`json.loads` object_pairs_hook that REFUSES a repeated member name
    instead of silently keeping the last one.

    Plain `json.loads` resolves `{"primary": "A", "primary": "B"}` to
    `{"primary": "B"}` -- and jsonschema then validates the ALREADY-COLLAPSED
    dict, so the strict schema below can never see the duplicate. That is the
    exact silent wrong-note routing this whole file exists to prevent: the
    operator reads their sidecar and sees `primary: "A"`, the vault links to
    B's note, and every gate is green. The same collapse can silently WIDEN a
    group (a second `members` array replacing the first) or replace the whole
    `groups` list.

    So it is refused here, before schema validation, for every object in the
    document. A duplicate key is never legitimate JSON authoring; nothing
    well-formed is newly rejected."""
    seen = set()
    for key, _value in pairs:
        if key in seen:
            raise _DuplicateKey(key)
        seen.add(key)
    return dict(pairs)


def _read_json(path, describe):
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise CanonLinkGroupsLoadError(f"could not read {describe} at {path}: {exc}")
    try:
        doc = json.loads(content.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except _DuplicateKey as exc:
        raise CanonLinkGroupsLoadError(
            f"{describe} at {path} repeats the object key {exc.args[0]!r} -- "
            "a duplicate key silently keeps only the LAST value, which would "
            "route a link to a note the file does not visibly name; write "
            "each key exactly once",
            offending=exc.args[0],
        )
    except UnicodeDecodeError as exc:
        raise CanonLinkGroupsLoadError(f"{describe} at {path} is not valid UTF-8: {exc}")
    except RecursionError as exc:
        raise CanonLinkGroupsLoadError(
            f"{describe} at {path} is nested too deeply to parse: {exc}"
        )
    except ValueError as exc:
        raise CanonLinkGroupsLoadError(f"{describe} at {path} is not valid JSON: {exc}")
    if _exceeds_depth(doc, MAX_NESTING_DEPTH):
        raise CanonLinkGroupsLoadError(
            f"{describe} at {path} nests deeper than {MAX_NESTING_DEPTH} levels"
        )
    return doc


def load_link_groups(path, entries, allow_absent=True, schema_path=None):
    """`{member_source_form: primary_source_form}` for every group in the
    sidecar at `path`, validated against `entries` (canon.json's own
    `entries{}` mapping, passed in rather than loaded here so this module
    stays a leaf and every caller keeps ONE canon read).

    `entries` is REQUIRED and positional -- there is no default -- because a
    forgotten argument must not silently turn membership validation off.

    An absent file yields `{}` when `allow_absent` (the default); a present
    but non-regular path (a directory, a dangling symlink) is always an
    error, never "absent".
    """
    path = Path(path)
    schema_path = Path(schema_path) if schema_path is not None else DEFAULT_SCHEMA_PATH
    if not isinstance(entries, dict):
        raise CanonLinkGroupsLoadError(
            "canon entries{} must be a mapping to validate link-group "
            f"membership against, got {type(entries).__name__}"
        )

    if not path.is_file():
        # `lexists`, not `exists`: a DANGLING symlink is a broken sidecar the
        # operator meant to have, not an absent one -- treating it as absent
        # would silently skip an identity pass they believe is applied.
        if not path.is_symlink() and not path.exists():
            if allow_absent:
                return {}
            raise CanonLinkGroupsLoadError(f"canon_link_groups.json not found at {path}")
        raise CanonLinkGroupsLoadError(
            f"canon_link_groups.json at {path} is not a regular file"
        )

    doc = _read_json(path, "canon_link_groups.json")
    if not isinstance(doc, dict):
        raise CanonLinkGroupsLoadError(
            f"canon_link_groups.json at {path} did not parse to an object "
            f"(got {type(doc).__name__})"
        )

    schema = _read_json(schema_path, "canon-link-groups.schema.json")
    validator = jsonschema.Draft202012Validator(schema)
    try:
        errors = sorted(validator.iter_errors(doc), key=lambda e: [str(p) for p in e.path])
    except RecursionError as exc:
        raise CanonLinkGroupsLoadError(
            f"canon_link_groups.json at {path} is nested too deeply to validate: {exc}"
        )
    if errors:
        first = errors[0]
        loc = "/".join(str(p) for p in first.path) or "<root>"
        raise CanonLinkGroupsLoadError(
            f"canon_link_groups.json at {path} failed schema validation at "
            f"'{loc}': {first.message}"
        )

    primary_by_source_form = {}
    for idx, group in enumerate(doc["groups"]):
        primary = group["primary"]
        members = group["members"]
        if primary not in members:
            raise CanonLinkGroupsLoadError(
                f"canon_link_groups.json at {path}: groups[{idx}].primary "
                f"{primary!r} is not one of its own members",
                offending=primary,
            )
        for member in members:
            if member not in entries:
                raise CanonLinkGroupsLoadError(
                    f"canon_link_groups.json at {path}: groups[{idx}] member "
                    f"{member!r} is not a canon.json entries{{}} key",
                    offending=member,
                )
            if member in primary_by_source_form:
                raise CanonLinkGroupsLoadError(
                    f"canon_link_groups.json at {path}: {member!r} appears in "
                    "more than one group",
                    offending=member,
                )
            primary_by_source_form[member] = primary
    return primary_by_source_form
