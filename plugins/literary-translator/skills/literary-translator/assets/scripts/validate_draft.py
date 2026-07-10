#!/usr/bin/env python3
"""validate_draft.py -- the false-green-gate: deterministic coverage/content
validator for a translated segment draft.

Generalizes the real, source-proven `validate_draft.py` from historiettes-t3
(battle-tested at ~75-segment scale) -- see references/false-green-gate.md
for the complete six-check spec this script implements almost directly.

False-green, for this domain: a validator reports OK while a defect --
dropped footnote content, a swapped verse, an empty translation, a stray
untranslated sentinel -- actually shipped. This script exists specifically to
make that impossible for the defect classes below. It is deterministic (no
LLM judgment call anywhere inside it) and is the gate a draft must clear
*before* it is ever handed to the codex reviewer -- the reviewer's job is
literary/accuracy judgment; this script's job is "did anything mechanically
get corrupted."

Reads `segpack_path(seg)` (the source) and `draft_path(seg)` (the draft under
test). Canonical paths (load-bearing, see references/ledger-and-resumability.md
-- both deliberately WITHOUT a target-language suffix, a divergence from the
real historiettes-t3 reference project's own `.ru.draft.json` naming; v1 has
exactly one target language per project, already recorded once in
profile.yml's target.language.code):

    draft_path(seg)   = {durable_root}/segments/{seg}.draft.json
    segpack_path(seg) = {durable_root}/segments/segpack_{seg}.json

## The six checks (see references/false-green-gate.md for the full prose)

  1. Block/footnote/verse KEY SETS are exact 1:1 with the source segpack --
     no silent omission, no silent extra.
  2. Per prose block: the MULTISET of placeholder sentinels (footnote-anchor
     / embedded-verse tokens) matches the source's multiset.
  3. Per standalone verse block: the translation equals THAT block's own
     placeholder, via a parent_block BIJECTION -- not flat set membership.
  4. Per footnote: non-empty, no untranslated-sentinel string, placeholder
     fidelity.
  5. Per verse: the exact required-content fields, derived from the
     resolved verse_policy.mode (references/verse-policy.md's six-mode
     table). `skip` exempts translated CONTENT only, never coverage.
  6. Sentinel-lite marker survival, body_refs_only apparatus_policy ONLY:
     every recorded body_ref_markers[] string must still appear, same
     multiset count, in that block's translated text.

## Structural self-check

Also runs a hand-rolled (no `jsonschema` dependency, matching the real
source project's own scripts) structural self-check of the draft file
against draft.schema.json's MODE-NEUTRAL container shape, before any of the
six checks run -- a draft that fails this can't safely be walked by the
content checks below (wrong container types would raise, not report a
clean defect list). 1.2.0 addition: `dispatch_token` (a run-scoped
freshness metadata string, OPTIONAL in draft.schema.json) is allowed but
never required here -- when present, only type-checked (must be a string);
when absent (e.g. a legacy pre-1.2.0 draft), that's not a structural
defect. Either way it is deliberately EXCLUDED from every one of the six
content checks below -- it carries no translated content, so it never
participates in coverage/placeholder/content-fidelity judgment. Presence+
correctness where it actually matters is enforced by the runtime freshness
gates (draft_ready.py --expect-token, ledger_update.py's run_token
precondition), not by this structural check.

## Adapt points (what generalizes this beyond the real source)

  - Verse-section content checks (check 5) branch on `verse_policy.mode`,
    read from profile.yml.
  - Footnote checks (checks 4 and 6) branch on `footnotes.apparatus_policy`,
    read from profile.yml.
  - The untranslated-sentinel string is read from
    `validation.untranslated_sentinel` in profile.yml -- NEVER hardcoded
    (the real source hardcodes a literal Russian string).
  - Placeholder tokens are matched by a format-neutral `⟦FNREF_N⟧` /
    `⟦VERSE_...⟧` pattern -- NOT the real source's
    `VERSE_V\\d+_[0-9a-f]{8}` internal-naming assumption, since a
    generalized segpack.schema.json's `vid` field is a free-form string,
    not guaranteed to follow that one project's own convention.

Usage: python3 validate_draft.py SEG   (e.g. seg05)
Exit 0 = clean, 1 = defects (printed), 2 = usage/environment error.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import NoReturn

try:
    import yaml
except ImportError:
    print(
        "ERROR: validate_draft.py requires the 'PyYAML' package to read "
        "profile.yml. Install with: pip install PyYAML (or: "
        "pip install -r requirements.txt from the literary-translator "
        "plugin's own directory).",
        file=sys.stderr,
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# Self-anchoring: this script always lives at {durable_root}/scripts/<name>.py.
# It never assumes cwd == durable_root, and never takes a --durable-root flag.
# ---------------------------------------------------------------------------
DURABLE_ROOT = Path(__file__).resolve().parents[1]
SEGMENTS_DIR = DURABLE_ROOT / "segments"

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


# Format-neutral placeholder sentinel: ⟦FNREF_N⟧ for footnote anchors,
# ⟦VERSE_...⟧ for embedded/standalone verse placeholders. The real source
# hardcodes VERSE_V\d+_[0-9a-f]{8} (its own internal vid+shortsha naming
# convention) -- deliberately widened here since segpack.schema.json's `vid`
# is a free-form string with no guaranteed shape across adapters/projects.
PH_RE = re.compile(r"⟦(?:FNREF_\d+|VERSE_[^⟧]+)⟧")

# Recognized verse_policy.mode enum, per profile.schema.json / references/verse-policy.md.
VERSE_MODES = frozenset({
    "full_rhymed_plus_literal",
    "full_rhymed_only",
    "rhythmic_approximation",
    "mixed_by_length",
    "literal_only",
    "skip",
})

# HTML-tag-boundary marker used only to recover approximate line breaks from
# source_html content for the "multi-line source -> non-1-line-rendering"
# check (5) -- deliberately NOT a full HTML parser (no bs4 dependency); a
# tag boundary is treated as a plausible line break, which is a reasonable
# approximation for the poem-block markup gutenberg_epub-style adapters emit.
_TAG_RE = re.compile(r"<[^>]+>")


def draft_path(seg):
    return SEGMENTS_DIR / f"{seg}.draft.json"


def segpack_path(seg):
    return SEGMENTS_DIR / f"segpack_{seg}.json"


def placeholders(text):
    """Sorted multiset of placeholder tokens in `text` (order-independent
    compare -- catches drop/dup/mangle, not intra-block reorder, which is
    deliberately left for the semantic codex review to catch)."""
    return sorted(PH_RE.findall(text or ""))


def _norm_ws(s):
    """Collapse all whitespace runs to single spaces -- so a mere re-wrap of
    the literal gloss (internal spaces -> newlines) can't masquerade as a
    distinct rhymed rendering."""
    return re.sub(r"\s+", " ", s or "").strip()


def _block_source_text(block):
    """A segpack block carries EITHER source_html (gutenberg_epub-style
    adapters) OR plain_text (plain_text-style adapters), per
    segpack.schema.json's anyOf. Prefer plain_text when present."""
    pt = block.get("plain_text")
    if pt:
        return pt
    return block.get("source_html") or ""


def _source_line_count(text):
    """Approximate the ORIGINAL source verse's line count, for check 5's
    "multi-line source -> non-1-line-rendering" rule. The real source reads
    a precomputed `n_line` field off its own segpack entries; the
    generalized segpack.schema.json does not carry one, so this derives an
    equivalent count from the parent block's own source text -- a tag
    boundary counts as a line break, so this works for both plain_text (real
    newlines) and source_html (poem lines wrapped in <br/>/<p> markup)."""
    if not text:
        return 0
    normalized = _TAG_RE.sub("\n", text)
    return len([ln for ln in normalized.splitlines() if ln.strip()])


# ---------------------------------------------------------------------------
# Profile resolution: this script takes ONLY a segment id on the CLI (no
# --profile flag, matching the real invocation
# `python3 {durable_root}/scripts/validate_draft.py SEG`). profile.yml's own
# location is not fixed relative to durable_root (durable_root MAY coincide
# with the project root, or MAY be elsewhere entirely), so it is resolved
# via the ownership marker Step 0a writes: {durable_root}/.literary-
# translator-root.json's own recorded owner_profile_path.
# ---------------------------------------------------------------------------

def _fatal(msg) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def load_profile():
    marker_path = DURABLE_ROOT / ".literary-translator-root.json"
    if not marker_path.is_file():
        _fatal(
            f"ownership marker not found: {marker_path} -- run Step 0a "
            f"(durable-root scaffolding) before validate_draft.py."
        )
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fatal(f"ownership marker at {marker_path} is not valid JSON: {exc}")

    owner_profile_path = marker.get("owner_profile_path") if isinstance(marker, dict) else None
    if not owner_profile_path:
        _fatal(f"ownership marker at {marker_path} has no owner_profile_path")

    profile_path = Path(owner_profile_path)
    if not profile_path.is_file():
        _fatal(f"profile.yml not found at {profile_path} (per {marker_path})")
    try:
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _fatal(f"profile.yml at {profile_path} is not valid YAML: {exc}")
    if not isinstance(profile, dict):
        _fatal(f"profile.yml at {profile_path} did not parse to a mapping")
    return profile


class ProfileConfig:
    """The exact profile.yml fields validate_draft.py's adapt points read --
    never hardcoded, never re-derived. See references/false-green-gate.md's
    "Adapt points" section."""

    def __init__(self, profile):
        try:
            verse_policy = profile["verse_policy"]
            self.verse_mode = verse_policy["mode"]
        except KeyError as exc:
            _fatal(f"profile.yml missing required field: verse_policy.{exc.args[0]}")
        self.threshold_lines = verse_policy.get("threshold_lines")

        if self.verse_mode not in VERSE_MODES:
            _fatal(
                f"profile.yml verse_policy.mode={self.verse_mode!r} is not one "
                f"of {sorted(VERSE_MODES)}"
            )
        if self.verse_mode == "mixed_by_length" and self.threshold_lines is None:
            _fatal(
                "profile.yml verse_policy.mode=mixed_by_length requires "
                "verse_policy.threshold_lines to be set (should have been "
                "caught fatally at Step 0 -- profile_validate.py)"
            )

        try:
            self.apparatus_policy = profile["footnotes"]["apparatus_policy"]
        except KeyError as exc:
            _fatal(f"profile.yml missing required field: footnotes.{exc.args[0]}")

        try:
            self.sentinel = profile["validation"]["untranslated_sentinel"]
        except KeyError as exc:
            _fatal(f"profile.yml missing required field: validation.{exc.args[0]}")


# ---------------------------------------------------------------------------
# Structural self-check against draft.schema.json -- hand-rolled, no
# jsonschema dependency (matches the real source project's own scripts).
# This is a MODE-NEUTRAL structural superset ONLY: container shapes, nothing
# that varies by verse_policy.mode/apparatus_policy (that is this script's
# own job, below, not the schema's).
# ---------------------------------------------------------------------------

# Table-driven container specs for check_draft_structure: (key, container_type,
# item_type, human_readable_description). Kept module-level so the required-key
# list stays in one place.
_DRAFT_CONTAINER_SPECS = [
    ("blocks",    dict, str,  "object of string values"),
    ("footnotes", dict, str,  "object of string values"),
    ("verses",    dict, dict, "object of object values"),
    ("names",     list, dict, "array of objects"),
    ("notes",     list, str,  "array of strings"),
]
_DRAFT_REQUIRED_KEYS = ["seg"] + [spec[0] for spec in _DRAFT_CONTAINER_SPECS]
# "dispatch_token" (1.2.0) is a run-scoped freshness metadata field --
# OPTIONAL, matching draft.schema.json's own (not-required) property.
# It is deliberately OUT OF SCOPE for this script's six content checks
# below; it is listed here only so its PRESENCE doesn't trip the
# "unexpected extra top-level keys" rejection -- absence is fine (a legacy
# pre-1.2.0 draft), presence is fine (type-checked below when present), the
# runtime freshness gates (draft_ready.py --expect-token, ledger_update.py's
# run_token precondition) are what actually enforce correctness.
_DRAFT_OPTIONAL_KEYS = ["dispatch_token"]
_DRAFT_ALLOWED_KEYS = _DRAFT_REQUIRED_KEYS + _DRAFT_OPTIONAL_KEYS


def check_draft_structure(draft):
    if not isinstance(draft, dict):
        return [f"draft.schema.json: draft root must be an object, got {type(draft).__name__}"]

    errs = [
        f"draft.schema.json: missing required key {k!r}"
        for k in _DRAFT_REQUIRED_KEYS if k not in draft
    ]
    if errs:
        # Can't safely type-check keys that aren't even present.
        return errs

    if not isinstance(draft["seg"], str):
        errs.append("draft.schema.json: 'seg' must be a string")
    if "dispatch_token" in draft and not isinstance(draft["dispatch_token"], str):
        errs.append("draft.schema.json: 'dispatch_token' must be a string when present")

    for key, container_type, item_type, desc in _DRAFT_CONTAINER_SPECS:
        value = draft[key]
        if not isinstance(value, container_type):
            errs.append(f"draft.schema.json: {key!r} must be an {desc}")
            continue
        items = value.values() if container_type is dict else value
        if not all(isinstance(item, item_type) for item in items):
            errs.append(f"draft.schema.json: {key!r} must be an {desc}")

    extra = set(draft) - set(_DRAFT_ALLOWED_KEYS)
    if extra:
        errs.append(f"draft.schema.json: unexpected extra top-level keys: {sorted(extra)}")
    return errs


def _load_json(path, label):
    """Returns (obj, error_message_or_None)."""
    if not path.exists():
        return None, f"{label} missing: {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{label} at {path} is not valid JSON: {exc}"


def _diff_report(errs, label, src_keys, draft_keys):
    """Append MISSING/EXTRA lines for a coverage check (blocks, footnotes,
    verses -- the three places the six-check spec demands exact 1:1 key sets).
    Args may be any iterables of hashables; caller need not pre-set()-ify."""
    src_set, draft_set = set(src_keys), set(draft_keys)
    missing = sorted(src_set - draft_set)
    extra = sorted(draft_set - src_set)
    if missing:
        errs.append(f"{label} MISSING: {missing}")
    if extra:
        errs.append(f"{label} EXTRA: {extra}")


def _verse_required_fields(mode, rv, n_line, sentinel, vid):
    """Check 5 for one verse entry, under the given effective mode. Returns a
    list of error strings (empty = clean)."""
    errs = []
    rendered = (rv.get("rendered") or "").strip()
    literal_gloss = (rv.get("literal_gloss") or "").strip()

    if mode == "skip":
        # Content requirement only is exempted here -- coverage/bijection
        # (checks 1 and 3) are enforced unconditionally by the caller,
        # regardless of mode. Nothing else to check for skip.
        pass
    elif mode == "literal_only":
        if not literal_gloss:
            errs.append(f"[{vid}] literal_gloss missing (verse_policy.mode=literal_only requires it)")
        if rendered:
            errs.append(
                f"[{vid}] unexpected 'rendered' field present under "
                f"verse_policy.mode=literal_only (no rhyme fields expected)"
            )
    elif mode in ("full_rhymed_only", "rhythmic_approximation"):
        if not rendered:
            errs.append(f"[{vid}] rendered missing (verse_policy.mode={mode} requires it)")
    elif mode == "full_rhymed_plus_literal":
        if not rendered:
            errs.append(f"[{vid}] rendered missing (verse_policy.mode=full_rhymed_plus_literal requires it)")
        if not literal_gloss:
            errs.append(f"[{vid}] literal_gloss missing (verse_policy.mode=full_rhymed_plus_literal requires it)")
        if rendered and literal_gloss and _norm_ws(rendered) == _norm_ws(literal_gloss):
            errs.append(
                f"[{vid}] rendered == literal_gloss up to whitespace "
                f"(paste/rewrap -- need a real rhymed rendering)"
            )
        if rendered and n_line >= 2 and len(rendered.splitlines()) < 2:
            errs.append(f"[{vid}] rendered is a single line for a {n_line}-line source verse")
    else:  # pragma: no cover -- guarded earlier by ProfileConfig / mixed_by_length resolution
        errs.append(f"[{vid}] INTERNAL: unrecognized effective verse mode {mode!r}")

    if sentinel and (sentinel in rendered or sentinel in literal_gloss):
        errs.append(f"[{vid}] untranslated sentinel present in verse")
    return errs


def validate(seg, cfg):
    sp = segpack_path(seg)
    src, err = _load_json(sp, "segpack")
    if err:
        return [err]
    if not isinstance(src, dict):
        return [f"segpack at {sp} must be a JSON object, got {type(src).__name__}"]

    dp = draft_path(seg)
    draft, err = _load_json(dp, "draft")
    if err:
        return [err]

    struct_errs = check_draft_structure(draft)
    if struct_errs:
        return struct_errs
    # check_draft_structure's own first check (line ~270 above) returns a
    # non-empty error list whenever draft isn't a dict, so an empty struct_errs
    # here guarantees draft passed that check -- mirrors the isinstance(src,
    # dict) guard used for the segpack a few lines up.
    assert isinstance(draft, dict), "check_draft_structure should have caught a non-dict draft"

    errs = []

    src_blocks_list = src.get("blocks") or []
    block_meta = {b["id"]: b for b in src_blocks_list}
    src_block_text = {bid: _block_source_text(b) for bid, b in block_meta.items()}

    # --- checks 1 (blocks) + 2 (prose multiset) + 3 (verse bijection) -------
    verses_list = src.get("verses") or []
    parent_block_claims = {}
    for v in verses_list:
        # Embedded verse (mount=="embedded"): a verse quoted INSIDE another
        # block (a PARA/QUOTE/FRONTBACK prose block, or a footnote-definition
        # block). There is no STANDALONE verse block in draft.blocks{} for
        # it, so the per-block placeholder bijection (check 3) does not
        # apply. Its placeholder survival is enforced elsewhere: by the
        # prose multiset (check 2) when the carrier is a blocks[] entry, or
        # by footnote placeholder-fidelity (check 4) when the carrier is a
        # footnote-def. Coverage + content are enforced by check 5 under
        # every mode. A NON-embedded verse whose parent_block is missing
        # from blocks[] is still a genuine SOURCE DEFECT below.
        if v.get("mount") == "embedded":
            continue
        parent_block_claims.setdefault(v["parent_block"], []).append(v)

    bid_to_verse_ph = {}
    for bid, claimants in parent_block_claims.items():
        if len(claimants) > 1:
            errs.append(
                f"SOURCE DEFECT: parent_block {bid!r} claimed by multiple "
                f"verses ({[c['vid'] for c in claimants]}) -- ambiguous "
                f"bijection, regenerate segpack"
            )
            continue
        if bid not in block_meta:
            errs.append(
                f"SOURCE DEFECT: verse {claimants[0]['vid']!r} parent_block "
                f"{bid!r} not found among this segpack's blocks -- regenerate segpack"
            )
            continue
        bid_to_verse_ph[bid] = claimants[0]["placeholder"]

    ru_blocks = draft.get("blocks", {})
    src_bids = set(block_meta)
    _diff_report(errs, "blocks", src_bids, ru_blocks)
    shared_bids = sorted(src_bids & set(ru_blocks))

    for bid in shared_bids:
        rutext = ru_blocks[bid] or ""
        if bid in bid_to_verse_ph:
            expected = bid_to_verse_ph[bid]
            if rutext.strip() != expected:
                errs.append(
                    f"[{bid}] VERSE block must equal its OWN placeholder "
                    f"{expected!r}, got {rutext.strip()[:40]!r}"
                )
            continue
        src_text = src_block_text.get(bid, "")
        sp_ph, rp_ph = placeholders(src_text), placeholders(rutext)
        if sp_ph != rp_ph:
            errs.append(f"[{bid}] placeholder mismatch: src={sp_ph} draft={rp_ph}")
        if not rutext.strip() and src_text.strip():
            errs.append(f"[{bid}] empty translation")
        if cfg.sentinel and cfg.sentinel in rutext:
            errs.append(f"[{bid}] untranslated sentinel present")

    # --- check 6: sentinel-lite body_ref_markers survival, body_refs_only --
    if cfg.apparatus_policy == "body_refs_only":
        for bid in shared_bids:
            markers = block_meta[bid].get("body_ref_markers") or []
            if not markers:
                continue
            rutext = ru_blocks[bid] or ""
            for marker, want_count in Counter(markers).items():
                got_count = rutext.count(marker)
                if got_count != want_count:
                    errs.append(
                        f"[{bid}] body_ref marker {marker!r} count mismatch: "
                        f"recorded={want_count} draft={got_count}"
                    )

    # --- check 4: footnote coverage + content --------------------------------
    src_fn = {str(f["n"]): f.get("source_text", "") for f in (src.get("footnotes") or [])}
    ru_fn = {str(k): v for k, v in draft.get("footnotes", {}).items()}
    _diff_report(errs, "footnotes", src_fn, ru_fn)
    for n in sorted(set(src_fn) & set(ru_fn)):
        rutext = ru_fn[n] or ""
        if not rutext.strip():
            errs.append(f"[FN:{n}] empty translation")
        if cfg.sentinel and cfg.sentinel in rutext:
            errs.append(f"[FN:{n}] untranslated sentinel present")
        sp_ph, rp_ph = placeholders(src_fn[n]), placeholders(rutext)
        if sp_ph != rp_ph:
            errs.append(f"[FN:{n}] placeholder mismatch: src={sp_ph} draft={rp_ph}")

    # --- check 5: verse coverage + policy-derived content completeness ------
    src_v = {v["vid"]: v for v in verses_list}
    ru_v = draft.get("verses", {})
    _diff_report(errs, "verses", src_v, ru_v)

    for vid in sorted(set(src_v) & set(ru_v)):
        v = src_v[vid]
        rv = ru_v[vid]
        if not isinstance(rv, dict):
            errs.append(f"[{vid}] verse entry must be an object, got {type(rv).__name__}")
            continue

        if v.get("mount") == "embedded":
            # Parent may be a footnote-def block (absent from this
            # segment's blocks[]) OR a prose block whose own source line
            # count is the whole carrier, not this inline verse -- either
            # way use the count segpack.py threaded from the manifest verse
            # node.
            n_line = v.get("n_line")
            if not isinstance(n_line, int) or isinstance(n_line, bool) or n_line < 0:
                n_line = 0
        else:
            # Standalone block-mount verse: derive from the parent block's
            # own source text exactly as before (behavior-preserving for
            # existing segpacks and every hand-built fixture, which carry no
            # mount).
            n_line = _source_line_count(src_block_text.get(v.get("parent_block"), ""))
        effective_mode = cfg.verse_mode
        if effective_mode == "mixed_by_length":
            effective_mode = (
                "full_rhymed_plus_literal" if n_line >= cfg.threshold_lines
                else "rhythmic_approximation"
            )

        errs.extend(_verse_required_fields(effective_mode, rv, n_line, cfg.sentinel, vid))

    return errs


def main():
    if len(sys.argv) != 2:
        print("usage: python3 validate_draft.py SEG", file=sys.stderr)
        sys.exit(2)
    seg = sys.argv[1]
    _seg_err = validate_seg(seg)
    if _seg_err:
        print(f"Error: {_seg_err}", file=sys.stderr)
        sys.exit(2)

    profile = load_profile()
    cfg = ProfileConfig(profile)

    errs = validate(seg, cfg)
    if errs:
        print(f"[{seg}] FAIL ({len(errs)} defects):")
        for e in errs:
            print("   -", e)
        sys.exit(1)

    draft = json.loads(draft_path(seg).read_text(encoding="utf-8"))
    print(
        f"[{seg}] OK  blocks={len(draft.get('blocks', {}))} "
        f"fn={len(draft.get('footnotes', {}))} verses={len(draft.get('verses', {}))} "
        f"-- coverage+placeholders+content clean"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
