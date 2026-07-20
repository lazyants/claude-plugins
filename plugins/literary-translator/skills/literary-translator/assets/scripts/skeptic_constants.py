#!/usr/bin/env python3
"""Single source of truth for every default value + shared string constant the
RFC #215 Phase-2 skeptic pass depends on.

Imported bare (``from skeptic_constants import ...``, via the SCRIPT_DIR-on-
``sys.path`` importlib loader every sibling script/test uses) by
``suspicion_scan.py``, ``skeptic_setup.py``, ``skeptic_ready.py``, and
``skeptic_report.py`` so no default is ever duplicated or "tuned during
implementation" in more than one place.

The JSON-Schema ``default:`` values under ``profile.schema.json``
``glossary.skeptic_pass`` MIRROR the ``*_DEFAULT`` constants here; a parity test
asserts they never drift (``profile_validate.py`` does not itself materialize
schema defaults, so the constants below are the real runtime defaults; an
enabled profile passes its values as explicit CLI overrides via the W-step).

This module is NOT a ``PLUGIN_BUNDLE_MEMBER`` (editing it must never re-translate
a converged segment) but IS hashed into ``producer_input_digest`` and the skeptic
``input_digest`` -- it governs the closure it belongs to (round-3 blocker 1).
"""

# --- Deterministic default values (mirrored by profile.schema.json defaults) ---
DISPERSION_THRESHOLD_DEFAULT = 12   # high_dispersion: distinct-seg count >= this
WINDOWS_PER_ENTITY_DEFAULT = 8      # max whole-block windows fed to the skeptic per entity
SAMPLE_CAP_DEFAULT = 50             # GLOBAL total sampled entries (largest-remainder across strata)
NEAR_THRESHOLD_DEFAULT = 0.15       # near_merge: keep pairs with (1 - SequenceMatcher.ratio()) <= this
NEAR_CAP_DEFAULT = 40               # near_merge: emit at most this many top pairs
NEAR_PAIR_BUDGET_DEFAULT = 5000     # near_merge: pre-enumeration materialization budget (logged truncation)

# --- Citation-block classification (all_citation risk class), keyed by source.format ---
# The set of block `type` tags that count as "citation" for each shipped adapter.
# custom / any format ABSENT from this map => all_citation DISABLED fail-safe
# (annotate CITATION_UNAVAILABLE_TAG; never guess a citation label from tag spelling).
CITATION_BLOCK_TYPES_BY_FORMAT = {
    "gutenberg_epub": ("FN", "QUOTE"),
    "plain_text": ("FN", "QUOTE"),
}

# --- Risk-class names (one canonical spelling each) ---
RISK_MERGE_PARTICIPANT = "merge_participant"
RISK_ESTABLISHED_OFFLINE = "established_offline"
RISK_SINGLETON = "singleton"
RISK_HIGH_DISPERSION = "high_dispersion"
RISK_ALL_CITATION = "all_citation"
RISK_NEAR_MERGE = "near_merge"
RISK_SAMPLED = "sampled"
# #243 (A2): a scope_in source_form whose fold_match_key collides with
# another scope_in source_form's (canon_senses.fold_collision_map(), computed
# over the UNION of canon.json entries + all canon_senses.json forms --
# split-only included -- then re-checked against this consumer's own
# scope_in so a collision with an out-of-scope/split-only-only competitor
# never fires this). suspicion_scan.py's own verse-occurrence side door
# (verse_occurrences() called per-form, fold-collision-unaware) can
# double-file ONE physical span to BOTH colliding forms while their
# block-origin counts get zeroed by occ_index.py's own fail-closed
# collision handling -- the two paths disagree in opposite directions,
# making singleton/high_dispersion/near_merge meaningless for these forms.
# ALWAYS flagged (never combined with the ordinary occurrence-count
# classes) so a colliding entry can never reach zero risk classes and
# silently never reach the skeptic.
RISK_FOLD_COLLISION = "fold_collision"
RISK_CLASSES = (
    RISK_MERGE_PARTICIPANT,
    RISK_ESTABLISHED_OFFLINE,
    RISK_SINGLETON,
    RISK_HIGH_DISPERSION,
    RISK_ALL_CITATION,
    RISK_NEAR_MERGE,
    RISK_SAMPLED,
    RISK_FOLD_COLLISION,
)

# --- Triage verdicts (ADVERSE-ONLY; there is deliberately NO confirmation verdict) ---
TRIAGE_ADVERSE = "adverse"
TRIAGE_PROPOSE_SPLIT = "propose_split"
TRIAGE_PROPOSE_RESCOPE = "propose_rescope"
TRIAGE_INSUFFICIENT_WINDOW = "insufficient_window"
TRIAGE_VERDICTS = (
    TRIAGE_ADVERSE,
    TRIAGE_PROPOSE_SPLIT,
    TRIAGE_PROPOSE_RESCOPE,
    TRIAGE_INSUFFICIENT_WINDOW,
)

# --- Annotation tags (recorded in worklist/triage `notes`, never a verdict) ---
CITATION_UNAVAILABLE_TAG = "citation_classification_unavailable"
VERSE_PARENT_UNRESOLVED_TAG = "verse_parent_unresolved"
ZERO_OCCURRENCE_TAG = "no_occurrences"
NEAR_BUDGET_TRUNCATED_TAG = "near_pair_budget_truncated"
# #243: stamped on every RISK_FOLD_COLLISION entry -- occurrence counting is
# skipped entirely for a fold-colliding form (never merely combined with the
# other classes' numbers), so a human reading the worklist sees WHY
# occurrence_refs is empty despite the entry appearing at all.
FOLD_COLLISION_OCCURRENCES_SUPPRESSED_TAG = "fold_collision_occurrences_suppressed"

# --- Deterministic ordering sentinels ---
# Absent/blank `category` -> a fixed sentinel so the sampled strata ordering is TOTAL.
NO_CATEGORY_SENTINEL = "\x00__no_category__"

# --- Scope filter ---
NON_IDENTITY_BASIS = "not_a_name"   # basis value that marks a non-identity-bearing entry

# --- Occurrence-ref origin (Part A worklist; distinguishes block vs embedded-verse) ---
OCC_ORIGIN_BLOCK = "block"            # offsets index into manifest.blocks[block].plain_text (citable window)
OCC_ORIGIN_VERSE_EMBEDDED = "verse_embedded"  # offsets index into verse.store[].plain_text (label-only, NOT citable)

# --- Verse mount normalization (shipped tolerant rule; segpack's own convention) ---
# Exactly "embedded" -> embedded (scan from verse.store); ANYTHING else, incl.
# absent, -> block-backed (owned by the block scan, NOT re-scanned).
VERSE_MOUNT_EMBEDDED = "embedded"

# --- Filenames + skeptic resume-domain run-dir layout (under durable_root) ---
SUSPICION_WORKLIST_FILENAME = "suspicion_worklist.json"        # {durable_root}/suspicion_worklist.json
SKEPTIC_TRIAGE_FILENAME = "skeptic_triage.json"               # {durable_root}/skeptic_triage.json (merged)
SKEPTIC_RUNS_SUBDIR = "skeptic/runs"                          # {durable_root}/skeptic/runs/{RUN_ID}/
SKEPTIC_AGGREGATE_MANIFEST_FILENAME = "assignments.json"      # aggregate assignment manifest in the run dir
SKEPTIC_INPUT_DIGEST_FILENAME = "input.digest"               # per-run recorded skeptic input_digest
SKEPTIC_FRAGMENT_PREFIX = "triage_"                          # run-scoped per-batch fragment: triage_{index}.json

# --- Schema filenames (assets/schemas/) ---
SUSPICION_WORKLIST_SCHEMA = "suspicion-worklist.schema.json"
SKEPTIC_ASSIGNMENT_SCHEMA = "skeptic-assignment.schema.json"
SKEPTIC_TRIAGE_SCHEMA = "skeptic-triage.schema.json"

# --- Frozen-input H1 tripwire descriptors (#243 round 8) ---
# The single authoritative enumeration of every frozen input the skeptic
# pass's H1 tamper tripwire covers. Before this table existed, the set was
# enumerated independently in THREE places that could silently drift apart:
# skeptic_setup.py's own hand-written stamp fields in the `assignments.json`
# it writes, skeptic_ready.py's own hand-written verifier table
# (`frozen_input_check()`'s `specs`), and this schema's declared
# `canon_sha256`/`manifest_sha256`/`senses_sha256` properties -- a fourth
# frozen input could be added to the stamper and the schema and simply
# omitted from the verifier table, and nothing would fail. Both
# `skeptic_setup.py` (the stamper) and `skeptic_ready.py`'s
# `frozen_input_check()` (the verifier) now iterate this SAME tuple to build
# their respective stamp-field dict / check table -- neither has a
# hand-maintained per-input line left to add without touching this tuple, so
# a frozen input cannot be wired into one side without automatically being
# wired into the other. `skeptic-assignment.schema.json`'s own
# `canon_sha256`/`manifest_sha256`/`senses_sha256` properties are NOT
# generated from this tuple -- JSON Schema is static data, this module is
# not a schema generator -- so a parity test asserts the schema's declared
# stamp-field set equals `{spec.stamp_field for spec in FROZEN_INPUT_SPECS}`;
# that test is the mechanism that catches a schema property added without a
# matching tuple entry (or vice versa).
# Each entry: (key, filename label, stamp field name in assignments.json).
FROZEN_INPUT_SPECS = (
    ("canon", "canon.json", "canon_sha256"),
    ("manifest", "manifest.json", "manifest_sha256"),
    ("senses", "canon_senses.json", "senses_sha256"),
)

# --- What FROZEN_INPUT_SPECS does NOT cover (#243 round 9) ---
# This tuple is the single source of truth for exactly two things: the
# stamp fields skeptic_setup.py writes into assignments.json, and the
# check table skeptic_ready.py's frozen_input_check() builds. Adding an
# entry here wires a frozen input into BOTH of those automatically -- but
# it wires it into nothing else. A new frozen input still needs its own
# hand-added line at every one of these separate sites, none of which
# reads this tuple:
#   - skeptic_setup.py's own three read_frozen_input_snapshot() calls at
#     the top of run() -- the actual (state, bytes) CAPTURE of
#     canon.json/manifest.json/canon_senses.json this tuple's stamps and
#     checks are computed FROM.
#   - suspicion_scan.compute_producer_input_digest() -- a fixed positional
#     signature (canon_state, canon_bytes, manifest_state, manifest_bytes,
#     senses_state, senses_bytes, plus resolved_params/
#     language_config_raw_bytes/script_dir) hashed into the worklist's
#     producer_input_digest freshness gate.
#   - skeptic_setup.compute_skeptic_input_digest() -- the same fixed
#     3-input shape, hashed into the skeptic resume digest
#     (resolve_skeptic_run()'s fresh-vs-resume decision).
#   - the `paths` dict inside skeptic_ready.py's frozen_input_check() that
#     maps this tuple's `key` to an actual filesystem Path (fails loudly
#     with KeyError if missed -- NOT silently).
#
# The consequence of missing one of these is asymmetric. Missing the
# `paths` entry fails LOUD (KeyError) the first time frozen_input_check()
# runs against the new key. Missing either digest function is SILENT and
# is the more dangerous half: the new input gets captured, stamped, and
# H1-tamper-checked correctly, but a change to it BEFORE setup runs
# doesn't move producer_input_digest or the skeptic resume digest -- a
# stale worklist/run still reads as fresh and gets (re)certified against
# the new state, exactly the stale-certified-as-fresh class this release
# exists to close, just moved to a boundary this tuple doesn't reach.
#
# Collapsing the two digest functions' fixed positional shape into
# something this tuple could also drive was evaluated and deliberately
# deferred (#243 round 9): both are called by fixed parameter name/
# position from dozens of sites across tests/skeptic_setup.test.py and
# tests/suspicion_scan.test.py, including tests that pin the exact
# NUL-byte framing between two specific adjacent parameters
# (tests/skeptic_setup.test.py's own canon_bytes=b"A"/manifest_bytes=b"BC"
# vs canon_bytes=b"AB"/manifest_bytes=b"C" boundary-collision pair) --
# generalizing the signature is a cross-file test-authoring change, not a
# same-file mechanical refactor, and was left out of this round rather
# than folded in under time pressure.
