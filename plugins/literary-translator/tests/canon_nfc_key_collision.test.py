"""tests/canon_nfc_key_collision.test.py -- coverage for issue #911: two
`entries{}` keys that are byte-different but the SAME string under Unicode
NFC normalization.

Hebrew points, Arabic harakat and Latin NFD accents carry distinct canonical
combining classes, so one mark run written in two different orders is two
byte-different strings that Unicode canonical ordering (NFC) folds into one.
The two render IDENTICALLY on screen -- no reviewer, diff, or editor can see
which row is the duplicate. Nothing upstream folds them: `entries{}` is keyed
by the source form exactly as the source text spells it, `_merge_batch`
compares `source_form` by plain string equality, and `canon-file.schema.json`
puts no `propertyNames` constraint on the map. Two such rows are therefore
two unrelated entries that may carry DIFFERENT `canonical_target_form`
values, and each segpack freezes whichever one its own extracted surface
happened to match -- the book ships one phrase two ways, every segment
obeying its own frozen contract. `canon_adjudication_audit.py`'s category-1
`surface_variant` finding DOES detect this exact pair and blocks on it
(unmaskable by `--advisory`) -- what it lacks is PERSISTENCE: it runs once,
before W3a, so a later merge or key rename can introduce the pair afterward,
and its finding is legitimately clearable with a `confirmed_ok` verdict
(the right answer to "two people sharing a spelling?", the wrong one here,
where the two forms are one string). The gap this closes is the absence of
a STANDING check on the write and read paths, not an absence of detection.

This is a dedicated file, deliberately not folded into an existing suite:
`tests/canon_correct_entry.test.py`'s own fixture-guard machinery matches at
FILE level, so adding these cases there could turn that file red for reasons
that have nothing to do with #911.

Covered:
  1. Two NFC-equal, byte-distinct keys with DIFFERENT targets -- refused on
     validate-only, naming both keys distinguishably.
  2. The same, with the SAME target (the silent-duplicate shape) -- also
     refused; the guard is on the KEYS, not on whether they agree.
  3. A lone non-NFC key with no twin -- still validates clean. The invariant
     refuses a COLLISION, never a form.
  4. `--merge-batches` introducing a new key that collides with an existing
     one -- refused, canon.json left byte-unchanged.
  4b. One fragment carrying BOTH colliding forms as accepted items -- refused
     by the same guard, proving the accumulating in-loop mapping is covered.
  5. THE IMPORTANT ONE: a canon holding TWO INDEPENDENT collision groups,
     repaired by two successive `--correct` `disposition:"remove"` calls.
     The regression lock on a deadlock an earlier design would have had.
  6. `--verify-merged` over a colliding canon -- `verified: false` and the
     collision named in `missing`, never an uncaught exception.
  7. Non-vacuity: a clean canon validates, and `_nfc_colliding_entry_keys` is
     called directly with three distinct keys and asserted to return [].
  8. THE COMPARATOR PIN: NFC-distinct keys that a casefold-based comparator
     would falsely collide must NOT be refused. Makes the plain-NFC design
     decision load-bearing rather than assumed.
  9. A 300-character target on the FIRST colliding row must not push the
     SECOND key out of the bounded report.
  10. The no-op exception: merging an already-frozen, unchanged batch into a
     canon that already holds a collision is REFUSED, not a silent no-op --
     intended fail-closed behaviour, not a regression.
  11. THE REAL KEY LENGTH: the actual #911 canon key is a 19-codepoint
     two-word phrase, not the 7-codepoint single-word FORM_A/FORM_B every
     other test here uses -- and at that real length an earlier message
     shape lost a key AND the repair route, which the shorter fixture never
     caught through two review rounds.
  12. A Latin-1 colliding key renders as valid, round-trippable JSON --
     regression lock on `ascii()`'s invalid `\\xXX` escape for U+0080-U+00FF.
  13. FIVE collision groups: every group the report SHOWS is complete (never
     one of its two keys), and the overflow note counts GROUPS, not lines.
  14. `--verify-merged` at the real 19-codepoint length keeps the pointer
     ("rerun canon_validate.py with no --verify-merged and no --batch and
     no --expect-source-forms-file ...") and the group COUNT, and
     deliberately does NOT carry the keys -- there is no ordering that fits
     both inside the second, whole-message 200-char cap.
  15. A single NFC equivalence class bigger than the whole report budget (9
     members): cut WITHIN the group with its own announced count, never
     silently by the generic element cap.
  16. `--init` on an EXISTING canon that already holds a collision -- the
     zero-candidate and `glossary.enabled: false` rejoin branches run no
     merge, so this read-only check is the only thing standing between a
     colliding canon and W3a. Refused, both keys named, canon.json left
     byte-unchanged.

Every behavioural assertion drives the real `canon_validate.py` as a
subprocess, exactly like its sibling suites. Assertions are on CONTRACT
(exit code, JSON payload keys, canon.json bytes) and on short, distinctive
message substrings -- never on a long exact stderr string.
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _canon_project_fixture import (  # noqa: E402
    accepted_item,
    load_canon_validate_module,
    make_project,
    read_canon,
    run_canon_init,
    run_canon_validate,
    run_script,
    write_fragment,
)

# ---------------------------------------------------------------------------
# The two colliding Hebrew forms the plan specifies: NFC-stable vs. its mark
# run reordered. Asserted below (at collection time) rather than assumed, so
# a future Unicode revision that changed this would fail the whole suite
# loudly instead of silently testing nothing.
# ---------------------------------------------------------------------------
FORM_A = "".join(chr(c) for c in (0x05D4, 0x05B7, 0x05E9, 0x05B5, 0x05BC, 0x05C1, 0x05DD))
FORM_B = "".join(chr(c) for c in (0x05D4, 0x05B7, 0x05E9, 0x05BC, 0x05C1, 0x05B5, 0x05DD))
assert FORM_A != FORM_B, "FORM_A and FORM_B must be byte-distinct to be a collision fixture"
assert unicodedata.normalize("NFC", FORM_A) == unicodedata.normalize("NFC", FORM_B), (
    "FORM_A and FORM_B must be NFC-equal, or this whole suite tests nothing"
)

# A second, independent collision group -- a different Hebrew word, its two
# reordered marks carrying different canonical combining classes (14 and 21)
# than FORM_A/FORM_B's (15 and 21), so this is not a re-derivation of the
# same pair.
FORM_C = "".join(chr(c) for c in (0x05D1, 0x05B7, 0x05D9, 0x05EA, 0x05B4, 0x05BC))
FORM_D = "".join(chr(c) for c in (0x05D1, 0x05B7, 0x05D9, 0x05EA, 0x05BC, 0x05B4))
assert FORM_C != FORM_D, "FORM_C and FORM_D must be byte-distinct to be a collision fixture"
assert unicodedata.normalize("NFC", FORM_C) == unicodedata.normalize("NFC", FORM_D), (
    "FORM_C and FORM_D must be NFC-equal, or this whole suite tests nothing"
)
assert {FORM_A, FORM_B}.isdisjoint({FORM_C, FORM_D}), (
    "the two collision groups must be independent, not the same pair twice"
)

# The REAL two-word phrase that motivated #911: FORM_A/FORM_B's word plus a
# second word, 19 codepoints total -- the length the actual reporting canon
# uses, not a single-word stand-in. Kept separate from FORM_A/FORM_B (which
# stay as-is, since every other test in this file was written against them)
# because this pair exists to catch what a shorter fixture cannot: at 19
# `json.dumps(..., ensure_ascii=True)`-escaped codepoints (6 chars each for a
# Hebrew point), a message shape that budgets by LINE rather than by KEY can
# lose a key or the repair instruction that a 7-codepoint word never would.
FULL_PHRASE_A = "".join(
    chr(c) for c in (0x05D4, 0x05B7, 0x05E9, 0x05B5, 0x05BC, 0x05C1, 0x05DD, 0x20,
                      0x05D9, 0x05B4, 0x05EA, 0x05B0, 0x05D1, 0x05B8, 0x05BC, 0x05E8, 0x05B7, 0x05DA, 0x05B0)
)
FULL_PHRASE_B = "".join(
    chr(c) for c in (0x05D4, 0x05B7, 0x05E9, 0x05BC, 0x05C1, 0x05B5, 0x05DD, 0x20,
                      0x05D9, 0x05B4, 0x05EA, 0x05B0, 0x05D1, 0x05B8, 0x05BC, 0x05E8, 0x05B7, 0x05DA, 0x05B0)
)
assert len(FULL_PHRASE_A) == len(FULL_PHRASE_B) == 19, "the fixture premise: this is the real 19-codepoint length"
assert FULL_PHRASE_A != FULL_PHRASE_B, "FULL_PHRASE_A and FULL_PHRASE_B must be byte-distinct to be a collision fixture"
assert unicodedata.normalize("NFC", FULL_PHRASE_A) == unicodedata.normalize("NFC", FULL_PHRASE_B), (
    "FULL_PHRASE_A and FULL_PHRASE_B must be NFC-equal, or this whole suite tests nothing"
)

# A Latin-1 collision: U+00E9 (LATIN SMALL LETTER E WITH ACUTE, precomposed)
# vs. 'e' + U+0301 (COMBINING ACUTE ACCENT, decomposed) -- NFC folds the
# second onto the first. U+00E9 sits in U+0080-U+00FF, the exact range where
# Python's `ascii()` builtin emits `\xXX` rather than a valid `\uXXXX` JSON
# escape (confirmed below): the regression this fixture locks.
LATIN1_KEY_COMPOSED = "caf" + chr(0x00E9)
LATIN1_KEY_DECOMPOSED = "cafe" + chr(0x0301)
assert LATIN1_KEY_COMPOSED != LATIN1_KEY_DECOMPOSED, (
    "LATIN1_KEY_COMPOSED and LATIN1_KEY_DECOMPOSED must be byte-distinct to be a collision fixture"
)
assert unicodedata.normalize("NFC", LATIN1_KEY_DECOMPOSED) == LATIN1_KEY_COMPOSED, (
    "LATIN1_KEY_DECOMPOSED must fold onto LATIN1_KEY_COMPOSED under NFC, or this whole suite tests nothing"
)
def _is_valid_json_string_literal(rendered: str) -> bool:
    try:
        json.loads(rendered)
        return True
    except json.JSONDecodeError:
        return False


assert not _is_valid_json_string_literal(ascii(LATIN1_KEY_COMPOSED)), (
    "this fixture's whole premise is that ascii()'s own rendering of U+00E9 "
    "(single-quoted, '\\xe9') is NOT valid JSON -- if a future Python ascii() "
    "changed that, this fixture would no longer exercise finding 1"
)

# Five independent collision groups, for the group-count overflow case.
# FORM_A/FORM_B and FORM_C/FORM_D above are groups 1 and 2; three more below,
# each a different base letter with two Hebrew points swapped whose
# canonical combining classes differ (10 vs 17, 10 vs 18, 11 vs 19) -- same
# construction as FORM_C/FORM_D, on letters and points not reused anywhere
# else in this file.
GROUP_E_A = "".join(chr(c) for c in (0x05D0, 0x05B0, 0x05B7))
GROUP_E_B = "".join(chr(c) for c in (0x05D0, 0x05B7, 0x05B0))
GROUP_F_A = "".join(chr(c) for c in (0x05D2, 0x05B0, 0x05B8))
GROUP_F_B = "".join(chr(c) for c in (0x05D2, 0x05B8, 0x05B0))
GROUP_G_A = "".join(chr(c) for c in (0x05D3, 0x05B1, 0x05B9))
GROUP_G_B = "".join(chr(c) for c in (0x05D3, 0x05B9, 0x05B1))
FIVE_GROUPS = (
    (FORM_A, FORM_B), (FORM_C, FORM_D), (GROUP_E_A, GROUP_E_B),
    (GROUP_F_A, GROUP_F_B), (GROUP_G_A, GROUP_G_B),
)
for _a, _b in FIVE_GROUPS:
    assert _a != _b and unicodedata.normalize("NFC", _a) == unicodedata.normalize("NFC", _b), (
        f"{_a!r}/{_b!r} must be a byte-distinct, NFC-equal pair"
    )
assert len({k for pair in FIVE_GROUPS for k in pair}) == 10, (
    "all five groups' keys must be pairwise distinct, or this is fewer than 5 real groups"
)

# ONE equivalence class bigger than the whole 8-element report budget: four
# Hebrew points of four DISTINCT canonical combining classes (15, 21, 24, 10)
# after one base letter permute into byte-distinct spellings that all fold to
# the SAME NFC form, since canonical reordering sorts by combining class
# regardless of input order. Nine of those permutations (of the 24 possible)
# are enough to exceed the budget; this is the fixture for the "one class
# bigger than the whole cap" defect the five-groups test (many SMALL groups)
# cannot exercise.
NINE_MEMBER_GROUP = (
    "".join(chr(c) for c in (0x05D1, 0x05B5, 0x05BC, 0x05C1, 0x05B0)),
    "".join(chr(c) for c in (0x05D1, 0x05B5, 0x05BC, 0x05B0, 0x05C1)),
    "".join(chr(c) for c in (0x05D1, 0x05B5, 0x05C1, 0x05BC, 0x05B0)),
    "".join(chr(c) for c in (0x05D1, 0x05B5, 0x05C1, 0x05B0, 0x05BC)),
    "".join(chr(c) for c in (0x05D1, 0x05B5, 0x05B0, 0x05BC, 0x05C1)),
    "".join(chr(c) for c in (0x05D1, 0x05B5, 0x05B0, 0x05C1, 0x05BC)),
    "".join(chr(c) for c in (0x05D1, 0x05BC, 0x05B5, 0x05C1, 0x05B0)),
    "".join(chr(c) for c in (0x05D1, 0x05BC, 0x05B5, 0x05B0, 0x05C1)),
    "".join(chr(c) for c in (0x05D1, 0x05BC, 0x05C1, 0x05B5, 0x05B0)),
)
assert len(set(NINE_MEMBER_GROUP)) == 9, "all nine members must be byte-distinct, or this is fewer than 9"
assert len({unicodedata.normalize("NFC", k) for k in NINE_MEMBER_GROUP}) == 1, (
    "all nine members must share exactly ONE NFC form, or this is not one equivalence class"
)


def _project(tmp_path) -> Path:
    root = make_project(tmp_path)
    proc = run_canon_init(root)
    assert proc.returncode == 0, f"--init failed:\n{proc.stdout}\n{proc.stderr}"
    return root


def _entry(source_form: str, target_form: str) -> dict:
    return {
        "source_form": source_form,
        "is_proper_name": True,
        "canonical_target_form": target_form,
        "basis": "transliterated",
        "confidence": "high",
    }


def seed_canon(root: Path, entries: dict) -> None:
    """Writes `entries` straight into canon.json's `entries{}`, preserving
    the real `generation_hashes` --init stamped. Bypasses the script
    entirely -- deliberately, since post-fix `--merge-batches`/`--correct`
    refuse to CREATE a collision, which is the whole point of this fixture:
    the only way to observe the READ-side guards is to place one on disk by
    hand."""
    canon = read_canon(root)
    canon["entries"] = entries
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")


def canon_bytes(root: Path) -> bytes:
    return (root / "canon.json").read_bytes()


def payload_of(proc) -> dict:
    return json.loads(proc.stdout)


def validate_only(root: Path):
    return run_canon_validate(root, allow_durable_sibling=False)


def escaped_key(key: str) -> str:
    """The exact rendering `_nfc_colliding_entry_keys` puts in a collision
    report for one key: `json.dumps(key, ensure_ascii=True)`, NOT Python's
    `ascii()` builtin -- the two agree on `\\uXXXX` escapes but differ on
    quote character (double vs. single) and on how U+0080-U+00FF renders
    (`ascii()` emits `\\xXX`, not valid JSON), so `ascii()` is not a stand-in
    needle here. Matching the script's own call keeps this helper from
    drifting out of sync with it the next time the rendering changes."""
    return json.dumps(key, ensure_ascii=True)


# ---------------------------------------------------------------------------
# 1. Two NFC-equal, byte-distinct keys, different targets
# ---------------------------------------------------------------------------


def test_colliding_keys_with_different_targets_refused_on_validate(tmp_path):
    """The core defect: two rows, same string on screen, different resolved
    names. Without the #911 guard, validate-only would pass this canon
    clean and the book would ship the name two ways."""
    root = _project(tmp_path)
    seed_canon(root, {FORM_A: _entry(FORM_A, "TargetOne"), FORM_B: _entry(FORM_B, "TargetTwo")})

    proc = validate_only(root)
    assert proc.returncode == 1, f"expected a refusal:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    assert payload["success"] is False, payload
    error = payload["error"]

    escaped_a, escaped_b = escaped_key(FORM_A), escaped_key(FORM_B)
    assert escaped_a != escaped_b, "the two escaped spellings must differ, or the message cannot distinguish them"
    assert escaped_a in error, error
    assert escaped_b in error, error


# ---------------------------------------------------------------------------
# 2. Same, with the SAME target -- the silent-duplicate shape
# ---------------------------------------------------------------------------


def test_colliding_keys_with_the_same_target_also_refused(tmp_path):
    """Two keys agreeing on `canonical_target_form` is a redundant row, not
    a safe one -- the guard fires on the KEYS being NFC-equal, regardless of
    whether the payloads happen to agree."""
    root = _project(tmp_path)
    seed_canon(root, {FORM_A: _entry(FORM_A, "SameTarget"), FORM_B: _entry(FORM_B, "SameTarget")})

    proc = validate_only(root)
    assert proc.returncode == 1, f"expected a refusal:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    assert payload["success"] is False, payload
    assert escaped_key(FORM_A) in payload["error"] and escaped_key(FORM_B) in payload["error"], payload["error"]


# ---------------------------------------------------------------------------
# 3. A lone non-NFC key with no twin -- still validates
# ---------------------------------------------------------------------------


def test_lone_non_nfc_key_without_a_twin_still_validates_clean(tmp_path):
    """The invariant refuses a COLLISION, never a non-NFC form on its own.
    The source text legitimately carries non-NFC spellings; only having two
    byte-different keys that fold to the same string is the defect."""
    root = _project(tmp_path)
    seed_canon(root, {FORM_B: _entry(FORM_B, "TargetTwo")})

    proc = validate_only(root)
    assert proc.returncode == 0, f"a lone non-NFC key must not be refused:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    assert payload["success"] is True, payload
    assert payload["entries_count"] == 1, payload


# ---------------------------------------------------------------------------
# 4. --merge-batches introducing a colliding key
# ---------------------------------------------------------------------------


def test_merge_batches_refuses_a_new_item_colliding_with_an_existing_entry(tmp_path):
    """A clean canon holding FORM_A only; a batch tries to merge FORM_B (its
    NFC twin) as a brand-new, unrelated accepted item. Nothing in
    `_merge_batch`'s per-item collision check can catch this -- FORM_B is
    not already a key -- only grouping the ACCUMULATED post-merge mapping by
    NFC form catches it, and it must do so before anything is written."""
    root = _project(tmp_path)
    seed_canon(root, {FORM_A: _entry(FORM_A, "TargetOne")})
    before = canon_bytes(root)

    fragment = write_fragment(root, [accepted_item(FORM_B, "TargetTwo")], name="colliding.json")
    proc = run_canon_validate(root, "--merge-batches", str(fragment))

    assert proc.returncode == 1, f"expected the collision guard to fire:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    assert payload["success"] is False, payload
    # Not a needle on the guard's own trailing wording: `_bounded_message`
    # caps each reported collision LINE at 200 chars, and the two escaped
    # spellings plus their targets already exhaust that budget here, same as
    # canon_correct_entry.test.py's collision pin. Assert what survives --
    # the outer "collision(s)" wording and both distinguishable keys.
    assert "collision" in payload["error"], payload["error"]
    assert escaped_key(FORM_A) in payload["error"] and escaped_key(FORM_B) in payload["error"], payload["error"]
    assert canon_bytes(root) == before, "a rejected merge still modified canon.json"


def test_merge_batches_refuses_one_fragment_carrying_both_colliding_forms(tmp_path):
    """Both halves of the collision arrive in the SAME fragment, against a
    canon that starts clean -- proving the guard sees the mapping
    `_merge_batch` accumulates across the whole batch loop, not just
    against what was already on disk."""
    root = _project(tmp_path)
    before = canon_bytes(root)

    fragment = write_fragment(
        root,
        [accepted_item(FORM_A, "TargetOne"), accepted_item(FORM_B, "TargetTwo")],
        name="both_colliding.json",
    )
    proc = run_canon_validate(root, "--merge-batches", str(fragment))

    assert proc.returncode == 1, f"expected the collision guard to fire:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    assert payload["success"] is False, payload
    assert "collision" in payload["error"], payload["error"]
    assert escaped_key(FORM_A) in payload["error"] and escaped_key(FORM_B) in payload["error"], payload["error"]
    assert canon_bytes(root) == before, "a rejected merge still modified canon.json"


# ---------------------------------------------------------------------------
# 5. THE IMPORTANT ONE -- two independent collision groups, repaired in turn
# ---------------------------------------------------------------------------


def test_correct_remove_repairs_two_independent_collision_groups_successively(tmp_path):
    """Regression lock on a deadlock an earlier design would have had: if
    `--correct` were gated on the same whole-file NFC-collision invariant
    that validate-only and --verify-merged enforce, then with TWO
    independent collision groups on disk, removing one member of the first
    group would still leave the second group's collision in the post-write
    state -- the pre-write validation would reject that post-state, and
    NEITHER removal could ever be persisted, because there is no way to fix
    both groups in a single --correct call (it handles exactly one entry per
    invocation).

    `--correct` is deliberately exempt from `_assert_no_nfc_colliding_entry_
    keys` for exactly this reason. This test proves the exemption actually
    unblocks repair: the FIRST removal must succeed even while the SECOND
    group's collision is still sitting in `entries{}`, and the canon must
    end up clean after both."""
    root = _project(tmp_path)
    canon_before = read_canon(root)
    entry_a, entry_b = _entry(FORM_A, "TargetOne"), _entry(FORM_B, "TargetTwo")
    entry_c, entry_d = _entry(FORM_C, "TargetThree"), _entry(FORM_D, "TargetFour")
    seed_canon(root, {FORM_A: entry_a, FORM_B: entry_b, FORM_C: entry_c, FORM_D: entry_d})

    # First removal: clears the FORM_A/FORM_B group while the FORM_C/FORM_D
    # group is still an unresolved collision on disk.
    doc1 = {
        "source_form": FORM_B,
        "disposition": "remove",
        "old_entry": entry_b,
        "reason": "duplicate spelling of the same name under a different mark order",
    }
    proc1 = run_canon_validate(
        root, "--correct", str(write_fragment(root, doc1, name="fix1.json")),
        allow_durable_sibling=False,
    )
    assert proc1.returncode == 0, (
        f"the FIRST removal must succeed despite the still-unresolved second "
        f"collision group, or repair deadlocks entirely:\n{proc1.stdout}\n{proc1.stderr}"
    )
    canon_mid = read_canon(root)
    assert FORM_B not in canon_mid["entries"]
    assert FORM_A in canon_mid["entries"] and FORM_C in canon_mid["entries"] and FORM_D in canon_mid["entries"]
    assert canon_mid["generation_hashes"] == canon_before["generation_hashes"], (
        "a correction must never restamp generation_hashes"
    )

    # Second removal: clears the FORM_C/FORM_D group.
    doc2 = {
        "source_form": FORM_D,
        "disposition": "remove",
        "old_entry": entry_d,
        "reason": "duplicate spelling of the same name under a different mark order",
    }
    proc2 = run_canon_validate(
        root, "--correct", str(write_fragment(root, doc2, name="fix2.json")),
        allow_durable_sibling=False,
    )
    assert proc2.returncode == 0, f"the second removal was refused:\n{proc2.stdout}\n{proc2.stderr}"

    canon_after = read_canon(root)
    assert set(canon_after["entries"].keys()) == {FORM_A, FORM_C}
    assert canon_after["generation_hashes"] == canon_before["generation_hashes"]

    proc3 = validate_only(root)
    assert proc3.returncode == 0, (
        f"canon.json must validate clean once both groups are repaired:\n"
        f"{proc3.stdout}\n{proc3.stderr}"
    )
    payload3 = payload_of(proc3)
    assert payload3["success"] is True, payload3
    assert payload3["entries_count"] == 2, payload3


# ---------------------------------------------------------------------------
# 6. --verify-merged reports the collision in `missing`, never an exception
# ---------------------------------------------------------------------------


# --verify-merged flags that take a VALUE (the following argv token is the
# value, not a separate flag). Hard-coded here rather than derived, because
# the regex that parses the OMITTED-flag set out of the live message has no
# way to know this -- it only sees flag names in prose, never their arity.
_VERIFY_MERGED_VALUE_TAKING_FLAGS = {"--batch", "--expect-source-forms-file"}


def _drop_argv_flags(argv, flags_to_drop):
    """Removes each flag in `flags_to_drop` from `argv` -- and, for a
    value-taking flag (`_VERIFY_MERGED_VALUE_TAKING_FLAGS`), its following
    value token too. Used to derive a recovery command by SUBTRACTION from
    the argv that was actually run, rather than hand-writing a second,
    possibly-drifted one."""
    result = []
    skip_next = False
    for tok in argv:
        if skip_next:
            skip_next = False
            continue
        if tok in flags_to_drop:
            if tok in _VERIFY_MERGED_VALUE_TAKING_FLAGS:
                skip_next = True
            continue
        result.append(tok)
    return result


def test_verify_merged_reports_the_collision_in_missing(tmp_path):
    """--verify-merged is disk-independent and reports failure through a
    SUCCESS-shaped payload (`verified: false`), never through
    CanonValidationError escaping to main()'s generic catch-all -- so this
    is the one call site that folds the same check into `missing` instead of
    raising it directly.

    Also the regression lock on a MAJOR the reviewer bot found, TWICE over.
    An earlier wording pointed the operator at `canon_validate.py ...
    validate-only` -- but `validate-only` is the NAME of the no-mode-flag
    mode, not a positional argparse accepts, so that exact invocation exits
    `unrecognized arguments: validate-only`. The next wording named the
    right two flags to drop (`--verify-merged`, `--batch`) but missed a
    third: the SHIPPED `--verify-merged` command (this module's own
    docstring example) also carries `--expect-source-forms-file`, which
    validate-only explicitly refuses -- so following that sentence still
    exited 2. Both times, a plain substring check on the message text
    ("does it say the right words") passed anyway, because the test drove
    a hand-written, always-correct recovery command instead of the one the
    message actually describes -- so an incomplete recipe could ship
    invisibly. This test now drives the FULL documented `--verify-merged`
    argv (all three flags, matching the module docstring's own example),
    PARSES the omitted-flag set out of the live message, and derives the
    recovery command by SUBTRACTING exactly those flags (and their values)
    FROM the argv that was actually run -- never a fresh, hand-written one.
    Only a subtraction that leaves something runnable proves the recipe is
    complete."""
    root = _project(tmp_path)
    seed_canon(root, {FORM_A: _entry(FORM_A, "TargetOne"), FORM_B: _entry(FORM_B, "TargetTwo")})
    empty_batch = write_fragment(root, [], name="empty_batch.json")
    # Empty on purpose: this flag's ARGV presence is what the earlier wording
    # missed, not its content -- an empty expected-forms list adds nothing to
    # `missing` beyond the NFC-collision entry this test is about.
    empty_manifest = write_fragment(root, [], name="expect_source_forms.json")

    verify_argv = (
        "--research-mode", "offline",
        "--verify-merged",
        "--batch", str(empty_batch),
        "--expect-source-forms-file", str(empty_manifest),
        "--allow-durable-sibling",
    )
    proc = run_script(root, "canon_validate.py", *verify_argv)
    assert proc.returncode == 1, f"expected verified:false to exit 1:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    assert payload["verified"] is False, payload
    joined = " ".join(payload["missing"])
    # `run_verify_merged` bounds EACH `missing` entry at 200 chars a SECOND
    # time -- on top of `_nfc_colliding_entry_keys`'s own per-line budgeting
    # -- by joining the whole multi-line report into ONE string and capping
    # THAT (measured 196 KB -> 2.4 MB unbounded before the cap existed). The
    # fixed pointer-plus-count prose ahead of the per-key lines already
    # exhausts that outer 200-char budget on its own, so -- unlike
    # validate-only, where each line gets its own budget -- the escaped keys
    # and the repair route do NOT survive here; only the invariant's own name
    # and the pointer to the mode that CAN print the keys do. This is still
    # the "reported through missing, not an exception" shape this test
    # exists to pin: no traceback, a bounded diagnostic line, and the FULL
    # per-key report is one validate-only run away.
    assert "NFC-colliding source_form key group(s)" in joined, payload["missing"]
    assert escaped_key(FORM_A) not in joined and escaped_key(FORM_B) not in joined, (
        f"the escaped keys were NOT expected to survive this outer cap -- if "
        f"they now do, this assertion (and its comment above) is stale: "
        f"{payload['missing']!r}"
    )

    # PARSE the recipe out of the live message, then RUN it -- do not just
    # assert its wording. `re.search` here fails loudly (None has no
    # .group()) if the pointer's shape changes at all, which is deliberate:
    # a shape change means this test's derivation needs a human look, not a
    # silently-passing pattern match.
    recipe = re.search(r"rerun canon_validate\.py (.+?) to print every colliding key:", joined)
    assert recipe is not None, f"could not find the recipe pointer in: {joined!r}"
    omitted_flags = set(re.findall(r"no (--[\w-]+)", recipe.group(1)))
    assert omitted_flags == {"--verify-merged", "--batch", "--expect-source-forms-file"}, (
        f"the advertised recipe now omits a different flag set than this "
        f"test executes -- update BOTH together: {recipe.group(1)!r}"
    )

    # Derive the recovery command by SUBTRACTING the parsed flags (and their
    # values) from the argv that was ACTUALLY run above -- never a
    # hand-written fresh one. This is the whole point: it proves the
    # subtraction (i.e. the recipe as literally stated) yields something
    # that runs, rather than proving some other, always-correct command does.
    recovery_argv = _drop_argv_flags(verify_argv, omitted_flags)
    assert recovery_argv == ["--research-mode", "offline", "--allow-durable-sibling"], (
        f"unexpected recovery argv after subtraction: {recovery_argv!r}"
    )
    recovery = run_script(root, "canon_validate.py", *recovery_argv)
    assert recovery.returncode != 0, (
        f"the advertised recipe, run as the argv it actually describes, did "
        f"not exit non-zero over the same colliding canon:\n"
        f"{recovery.stdout}\n{recovery.stderr}"
    )
    recovery_payload = payload_of(recovery)
    recovery_error = recovery_payload["error"]
    assert escaped_key(FORM_A) in recovery_error and escaped_key(FORM_B) in recovery_error, (
        f"the advertised recipe ran but did not recover both keys: {recovery_error!r}"
    )


# ---------------------------------------------------------------------------
# 7. Non-vacuity
# ---------------------------------------------------------------------------


def test_clean_canon_with_several_ordinary_keys_validates(tmp_path):
    """A plain, uncontroversial canon must still pass -- the guard must not
    fire on distinct keys that merely share a base letter or script."""
    root = _project(tmp_path)
    seed_canon(
        root,
        {
            "Alice": _entry("Alice", "Alice"),
            "Bob": _entry("Bob", "Bob"),
            "Carol": _entry("Carol", "Carol"),
        },
    )

    proc = validate_only(root)
    assert proc.returncode == 0, f"an ordinary canon must validate clean:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    assert payload["success"] is True, payload
    assert payload["entries_count"] == 3, payload


def test_nfc_colliding_entry_keys_returns_empty_for_three_distinct_keys():
    """A second black-box clean validation would prove nothing about whether
    the grouping loop actually iterated (an empty result and a
    never-entered loop look identical from outside). Calling the helper
    DIRECTLY, in-process, with a mapping of three genuinely distinct keys is
    what shows the grouping actually ran and produced no false positive."""
    module = load_canon_validate_module()
    entries = {
        "Alice": {"canonical_target_form": "Alice"},
        "Bob": {"canonical_target_form": "Bob"},
        FORM_A: {"canonical_target_form": "TargetOne"},
    }
    assert module._nfc_colliding_entry_keys(entries) == []


# ---------------------------------------------------------------------------
# 8. THE COMPARATOR PIN -- plain NFC, never normalize_form()
# ---------------------------------------------------------------------------


def test_nfc_distinct_keys_a_casefolding_comparator_would_falsely_collide(tmp_path):
    """Pins the comparator itself, not just its output on the fixture cases
    above. `canon_senses.normalize_form()` is this codebase's OWN sibling
    normalizer for fuzzy grouping/matching -- NFC-normalize, THEN casefold,
    THEN collapse whitespace. Python's own casefold table maps the German
    sharp s to a doubled "s": 'Straße'.casefold() == 'Strasse'.casefold()
    == 'strasse', even though the two strings are already NFC-NORMAL and
    NFC-DISTINCT (`unicodedata.normalize("NFC", ...)` is a no-op on both --
    there is no combining-mark reordering here at all).

    A reviewer showed that every one of this suite's other nine tests would
    still pass if `_nfc_colliding_entry_keys` were rewritten to compare via
    `normalize_form` instead of plain NFC -- none of them puts a
    casefold-only collision on disk. This is the one case that goes RED
    under `normalize_form` (it would falsely refuse two different,
    legitimately spelled names as a "collision") and GREEN under plain NFC
    (they are simply two different entries). Without this test, the
    comparator choice is an assumption; with it, swapping in the wrong
    normalizer breaks a named test rather than shipping silently."""
    assert unicodedata.normalize("NFC", "Straße") == "Straße", (
        "the fixture premise: NFC must be a no-op on the eszett spelling"
    )
    assert "Straße".casefold() == "Strasse".casefold(), (
        "the fixture premise: casefold must be what falsely equates these two"
    )
    root = _project(tmp_path)
    seed_canon(root, {"Strasse": _entry("Strasse", "RueUn"), "Straße": _entry("Straße", "RueDeux")})

    proc = validate_only(root)
    assert proc.returncode == 0, (
        f"two NFC-distinct, differently-spelled names must not be refused "
        f"as an NFC collision:\n{proc.stdout}\n{proc.stderr}"
    )
    payload = payload_of(proc)
    assert payload["success"] is True, payload
    assert payload["entries_count"] == 2, payload


# ---------------------------------------------------------------------------
# 9. A long target must not push the second key out of the bounded report
# ---------------------------------------------------------------------------


def test_a_300_char_target_does_not_push_the_second_key_out_of_the_report(tmp_path):
    """The reviewer's concrete failure scenario: `canonical_target_form` is
    an unconstrained schema string, so a long one on the FIRST colliding
    row's line could consume that line's whole 200-char report budget and
    crowd out its own key -- a refusal that names only one of the two rows
    it is refusing. `_nfc_colliding_entry_keys` now renders ONE LINE PER KEY
    (each key gets its OWN `_bounded_message` budget, separate from every
    other key's) and caps each target at 40 chars, specifically so one
    row's long target cannot cost another row's key its budget; this test
    is the regression lock on that per-key-line layout."""
    root = _project(tmp_path)
    long_target = "X" * 300
    seed_canon(
        root,
        {FORM_A: _entry(FORM_A, long_target), FORM_B: _entry(FORM_B, "Short")},
    )

    proc = validate_only(root)
    assert proc.returncode == 1, f"expected a refusal:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    assert payload["success"] is False, payload
    assert escaped_key(FORM_A) in payload["error"] and escaped_key(FORM_B) in payload["error"], (
        f"a 300-char target on the first row pushed the second key out of "
        f"the report: {payload['error']!r}"
    )


# ---------------------------------------------------------------------------
# 10. The no-op exception -- merging into an already-colliding canon
# ---------------------------------------------------------------------------


def test_merge_batches_refuses_an_unchanged_resubmission_into_a_colliding_canon(tmp_path):
    """`_merge_batch`'s own docstring promises that an identical
    re-submission of already-frozen items is a silent no-op -- true when
    `entries{}` is clean, but NOT when it already holds an NFC collision:
    the #911 scan reads the ACCUMULATED mapping, which starts as whatever is
    already on disk, so a canon that already resolves one phrase two ways
    refuses every further merge until it is repaired, whatever the batch
    contains. This is INTENDED fail-closed behaviour, not a regression --
    merging more into a canon in this state compounds the defect rather
    than deferring it -- so the refusal must name the repair route
    (`--correct` `disposition:"remove"`), since the operator's batch here is
    otherwise blameless: it changes nothing.

    On THIS path specifically -- unlike the read-side modes -- the merge
    refusal is the ONLY output produced; there is no fuller report (a
    validate-only run) behind it. So the repair instruction
    (`--correct disposition:"remove"`) MUST survive `_bounded_message`'s
    200-char per-line cap here, or the operator is left refused with no
    idea what to do. A future edit that lengthens this message (a longer
    verb, a restored targets clause, ...) must go red here rather than
    silently drop the instruction again."""
    root = _project(tmp_path)
    entry_a, entry_b = _entry(FORM_A, "TargetOne"), _entry(FORM_B, "TargetTwo")
    seed_canon(root, {FORM_A: entry_a, FORM_B: entry_b})
    before = canon_bytes(root)

    # Byte-identical resubmission: same source_forms, same targets, nothing
    # a plain per-item comparison would ever flag as a collision.
    fragment = write_fragment(
        root,
        [accepted_item(FORM_A, "TargetOne"), accepted_item(FORM_B, "TargetTwo")],
        name="noop_resubmit.json",
    )
    proc = run_canon_validate(root, "--merge-batches", str(fragment))

    assert proc.returncode == 1, (
        f"an unchanged resubmission into an already-colliding canon must be "
        f"refused, not treated as a no-op:\n{proc.stdout}\n{proc.stderr}"
    )
    payload = payload_of(proc)
    assert payload["success"] is False, payload
    # MEASURED, not assumed: the merge-path collision line now omits the
    # targets clause (`include_targets=False`) specifically so the repair
    # instruction fits -- both escaped keys AND the repair route survive the
    # 200-char cap (227 chars total here, comfortably under it).
    assert "collision" in payload["error"], payload["error"]
    assert escaped_key(FORM_A) in payload["error"] and escaped_key(FORM_B) in payload["error"], payload["error"]
    assert '--correct' in payload["error"], payload["error"]
    assert 'disposition:"remove"' in payload["error"], payload["error"]
    assert canon_bytes(root) == before, "a refused resubmission still modified canon.json"


# ---------------------------------------------------------------------------
# 11. The real key length -- not the single-word stand-in
# ---------------------------------------------------------------------------


def test_merge_batches_survives_the_real_19_codepoint_phrase_length(tmp_path):
    """FORM_A/FORM_B (used by every other test in this file) is a single
    7-codepoint word. The actual #911 canon key is a two-word phrase, 19
    codepoints -- and length matters here in a way it does not for most
    string handling: escaped as `json.dumps(..., ensure_ascii=True)`, a
    single Hebrew codepoint costs SIX characters, so a message shape that
    gives one LINE a fixed budget for
    a whole collision group runs out of room at ordinary name lengths, not
    just extreme ones.

    This is not a hypothetical: an earlier shape of this refusal named both
    keys for the 7-codepoint FORM_A/FORM_B pair and named NEITHER key, nor
    the repair instruction, for this exact 19-codepoint phrase -- and every
    other test in this file, built only on the shorter pair, passed clean
    through two review rounds while that was true. A fixture shorter than
    the real data hid the defect; this test exists so it cannot again."""
    root = _project(tmp_path)
    seed_canon(root, {FULL_PHRASE_A: _entry(FULL_PHRASE_A, "TargetOne")})
    before = canon_bytes(root)

    fragment = write_fragment(
        root, [accepted_item(FULL_PHRASE_B, "TargetTwo")], name="real_length_colliding.json"
    )
    proc = run_canon_validate(root, "--merge-batches", str(fragment))

    assert proc.returncode == 1, f"expected the collision guard to fire:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    assert payload["success"] is False, payload
    error = payload["error"]
    escaped_a, escaped_b = escaped_key(FULL_PHRASE_A), escaped_key(FULL_PHRASE_B)
    assert escaped_a != escaped_b, "the two escaped spellings must differ, or the message cannot distinguish them"
    assert escaped_a in error and escaped_b in error, (
        f"at the real 19-codepoint phrase length, at least one key was lost "
        f"from the refusal: {error!r}"
    )
    assert '--correct' in error and 'disposition:"remove"' in error, (
        f"at the real 19-codepoint phrase length, the repair route was lost "
        f"from the refusal: {error!r}"
    )
    assert canon_bytes(root) == before, "a rejected merge still modified canon.json"


# ---------------------------------------------------------------------------
# 12. A Latin-1 key must render as a PARSEABLE JSON string
# ---------------------------------------------------------------------------


def test_a_latin1_colliding_key_renders_as_valid_json(tmp_path):
    """Regression lock on a real defect the security pass found: the report
    line is documented as copy-pasteable into a `--correct` document, and
    that document is JSON. Python's `ascii()` builtin emits `\\xXX` for
    U+0080-U+00FF (confirmed at module load: `ascii(LATIN1_KEY_COMPOSED)` is
    not valid JSON), which is not a legal JSON escape -- so any key holding
    a Latin-1 character, one of the very classes an NFC/NFD accent check
    exists for, produced a line that could not be pasted back in. The fix
    renders keys with `json.dumps(key, ensure_ascii=True)` instead; this
    test proves the escaped key round-trips through `json.loads` back to
    the exact original string, not merely that some rendering appears."""
    root = _project(tmp_path)
    seed_canon(
        root,
        {
            LATIN1_KEY_COMPOSED: _entry(LATIN1_KEY_COMPOSED, "TargetOne"),
            LATIN1_KEY_DECOMPOSED: _entry(LATIN1_KEY_DECOMPOSED, "TargetTwo"),
        },
    )

    proc = validate_only(root)
    assert proc.returncode == 1, f"expected a refusal:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    error = payload["error"]

    quoted = re.findall(r'= ("(?:[^"\\]|\\.)*")', error)
    assert len(quoted) == 2, f"expected exactly two rendered key lines: {error!r}"
    parsed = {json.loads(q) for q in quoted}
    assert parsed == {LATIN1_KEY_COMPOSED, LATIN1_KEY_DECOMPOSED}, (
        f"the rendered key(s) did not round-trip through json.loads back to "
        f"the original strings: {quoted!r} parsed to {parsed!r}"
    )


# ---------------------------------------------------------------------------
# 13. Five collision groups -- every SHOWN group is complete, count the groups
# ---------------------------------------------------------------------------


def _completeness(text: str, groups) -> tuple:
    """For each (key_a, key_b) group, checks whether BOTH escaped keys
    appear somewhere in `text`. Returns (complete_count, partial_groups) --
    `partial_groups` is every group where exactly one of its two keys
    appears, which is the shape a mid-group truncation produces and must
    never happen on any field."""
    complete = 0
    partial = []
    for key_a, key_b in groups:
        has_a = escaped_key(key_a) in text
        has_b = escaped_key(key_b) in text
        if has_a and has_b:
            complete += 1
        elif has_a or has_b:
            partial.append((key_a, key_b))
    return complete, partial


def test_five_collision_groups_each_shown_group_is_complete(tmp_path):
    """Regression lock on the second defect: before this fix, the report was
    capped at 8 ELEMENTS (`_MAX_LISTED_PROBLEMS`) with no notion of a
    'group' -- so from three groups up, the cap could land mid-group and
    silently drop a group's SECOND key, and the overflow note counted LINES
    dropped rather than collision groups. The fix enumerates as many WHOLE
    groups as fit that SAME 8-element budget (reserving one slot for the
    overflow note), a group emitted whole or not at all: with 2-member
    groups that is 2 complete groups (a header line plus 2 key lines each,
    6 elements) plus the overflow note, 7 elements total, comfortably under
    the cap -- MEASURED, not assumed; see the assertions below.

    This is ALSO the regression lock on a second, independently-discovered
    defect: `payload["offending"]` is a SEPARATE field, capped by
    `CanonValidationError`'s own generic, group-BLIND `_MAX_LISTED_PROBLEMS`
    -- it does not know what a 'group' is at all. Before the group budget
    was DERIVED from that same element cap (rather than a group count
    picked independently), `error`'s own text could stay whole while
    `offending` truncated mid-group under it, silently. Checking both
    fields here is what actually locks that shared budget in place. Five
    independent groups is the smallest number that exercises the cap (2
    shown complete, 3 overflowed) while still being able to tell 'group'
    apart from 'line' in the count."""
    root = _project(tmp_path)
    entries = {}
    for i, (key_a, key_b) in enumerate(FIVE_GROUPS):
        entries[key_a] = _entry(key_a, f"Target{i}A")
        entries[key_b] = _entry(key_b, f"Target{i}B")
    seed_canon(root, entries)

    proc = validate_only(root)
    assert proc.returncode == 1, f"expected a refusal:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    error = payload["error"]

    assert "holds 5 NFC-colliding source_form key group(s)" in error, error

    complete, partial = _completeness(error, FIVE_GROUPS)
    assert partial == [], (
        f"a group appeared in `error` with only ONE of its two keys -- the "
        f"cap landed mid-group: {partial!r} in {error!r}"
    )
    assert complete == 2, (
        f"expected exactly 2 complete groups in `error` under the derived "
        f"element budget, got {complete}: {error!r}"
    )
    assert "... and 3 more collision group(s)" in error, (
        f"the overflow note must count the 3 left-out GROUPS, not lines: {error!r}"
    )

    # `offending` is the field this test's second defect was found in: it is
    # NOT built from the same string as `error` -- CanonValidationError caps
    # it independently and it is whitespace-flattened by `_bounded_message`
    # (the "    = " indent collapses to a single space), so match on the
    # escaped key content, never on layout.
    offending_text = " ".join(payload["offending"])
    off_complete, off_partial = _completeness(offending_text, FIVE_GROUPS)
    assert off_partial == [], (
        f"a group appeared in `offending` with only ONE of its two keys -- "
        f"this is the defect this test exists to lock shut: "
        f"{off_partial!r} in {payload['offending']!r}"
    )
    assert off_complete == 2, (
        f"expected exactly 2 complete groups in `offending`, got "
        f"{off_complete}: {payload['offending']!r}"
    )
    assert "... and 3 more collision group(s)" in offending_text, (
        f"`offending`'s own overflow note must also count the 3 left-out "
        f"GROUPS: {payload['offending']!r}"
    )


# ---------------------------------------------------------------------------
# 14. --verify-merged at the real key length -- pointer and count survive
# ---------------------------------------------------------------------------


def test_verify_merged_at_the_real_phrase_length_keeps_the_pointer_and_count(tmp_path):
    """At 19 codepoints, `--verify-merged`'s SECOND 200-char cap (over the
    whole exception string, on top of `_nfc_colliding_entry_keys`'s own
    per-line budgeting) cannot preserve any key -- there is no ordering
    that fits a real collision report inside 200 characters, escaped or
    not. The message therefore LEADS with a pointer to a working recipe
    ('rerun canon_validate.py with no --verify-merged and no --batch and no
    --expect-source-forms-file ...') plus the group COUNT, deliberately
    choosing to lose the keys rather than the instruction of where to find
    them. Do NOT assert the keys appear here; they cannot, by design -- a
    future edit that "fixes" this by moving the detail forward would evict
    the pointer instead, silently. (The pointer's own correctness -- that
    the three-flag recipe it names actually runs -- is locked separately,
    in test_verify_merged_reports_the_collision_in_missing, by parsing and
    EXECUTING it; this test only needs the pointer's wording to still be
    present, kept minimal on purpose so the execution test carries the
    weight.)"""
    root = _project(tmp_path)
    seed_canon(
        root,
        {FULL_PHRASE_A: _entry(FULL_PHRASE_A, "TargetOne"), FULL_PHRASE_B: _entry(FULL_PHRASE_B, "TargetTwo")},
    )
    empty_batch = write_fragment(root, [], name="empty_batch_real_length.json")

    proc = run_canon_validate(root, "--verify-merged", "--batch", str(empty_batch))
    assert proc.returncode == 1, f"expected verified:false to exit 1:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    assert payload["verified"] is False, payload
    assert len(payload["missing"]) >= 1, payload["missing"]
    first = payload["missing"][0]
    assert (
        "rerun canon_validate.py with no --verify-merged and no --batch and "
        "no --expect-source-forms-file"
    ) in first, first
    assert "holds 1 NFC-colliding source_form key group(s)" in first, first
    assert escaped_key(FULL_PHRASE_A) not in first and escaped_key(FULL_PHRASE_B) not in first, (
        f"the escaped keys were not expected to survive the second cap at "
        f"this length either -- if they now do, this assertion is stale: {first!r}"
    )


# ---------------------------------------------------------------------------
# 15. A single group bigger than the whole report budget
# ---------------------------------------------------------------------------


def test_nine_member_group_cuts_within_the_group_not_by_the_generic_cap(tmp_path):
    """Regression lock on a MINOR the reviewer bot found: the whole-group
    rule only checked whether a group fit the remaining budget when
    `collisions` (the list being built) was already non-empty -- so an
    OVERSIZED FIRST group was appended whole regardless of size, and the
    generic 8-element cap downstream then cut it apart with nothing saying
    so. This is exactly the mid-group truncation the whole-group rule
    exists to prevent, reachable because a single NFC equivalence class can
    itself exceed the budget: four Hebrew points of four distinct
    canonical combining classes permute into far more than 8 byte-distinct
    spellings of one string (24 possible; this fixture uses 9).

    The fix cuts an oversized group HERE, inside `_nfc_colliding_entry_
    keys`, and appends its OWN 'more key(s) in this group' note -- so the
    withheld count is always announced, and the generic cap downstream
    never has to act (a group is now always small enough to fit once it is
    emitted). This test's job is to prove NEITHER cap silently cut it: the
    per-group note announces the true count, the shown-plus-withheld count
    adds up to 9, and no GENERIC '... and N more collision group(s)' note
    appears -- if one did, it would mean the group-cap missed and the
    generic element cap did the cutting instead, un-announced.

    The five-groups test above cannot exercise this: many SMALL groups
    trip the GROUP-count overflow, never the single-group-too-big path."""
    root = _project(tmp_path)
    entries = {key: _entry(key, f"Target{i}") for i, key in enumerate(NINE_MEMBER_GROUP)}
    seed_canon(root, entries)

    proc = validate_only(root)
    assert proc.returncode == 1, f"expected a refusal:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    offending = payload["offending"]
    offending_text = " ".join(offending)

    assert "collision group(s)" not in offending_text, (
        f"a GENERIC '... and N more collision group(s)' note appeared -- "
        f"this means the single oversized group was NOT cut by its own "
        f"rule and the generic element cap silently cut it instead: "
        f"{offending!r}"
    )

    withheld_match = re.search(r"\.\.\. and (\d+) more key\(s\) in this group", offending_text)
    assert withheld_match is not None, (
        f"expected a '... and N more key(s) in this group' note announcing "
        f"the withheld count, found none: {offending!r}"
    )
    withheld = int(withheld_match.group(1))

    shown = sum(1 for key in NINE_MEMBER_GROUP if escaped_key(key) in offending_text)
    assert shown + withheld == 9, (
        f"shown ({shown}) + withheld ({withheld}) must account for all 9 "
        f"members: {offending!r}"
    )
    assert shown >= 1, f"expected at least one member key actually shown: {offending!r}"
    assert len(offending) <= 8, (
        f"`offending` must still respect the generic 8-element cap it was "
        f"measured against: {offending!r}"
    )


# ---------------------------------------------------------------------------
# 16. --init refuses a pre-existing collision on the zero-candidate rejoin
# ---------------------------------------------------------------------------


def test_init_refuses_a_pre_existing_collision_on_the_zero_candidate_rejoin(tmp_path):
    """`--init` is CREATE-ONLY: on an existing canon.json it used to return
    straight away (`created: false`) without opening the file at all. That
    made it the one blind spot in this whole invariant, because `--init` is
    not an edge case here -- it is the ONLY mode the documented zero-
    candidate and `glossary.enabled: false` W3 SKIP branches ever invoke.
    Both branches run no merge, so `_merge_batch`'s own collision guard
    never fires on them; and while `canon_adjudication_audit.py --check
    --advisory` DOES detect this exact pair afterward, its finding is a
    review-queue item, clearable with a `confirmed_ok` verdict, and it runs
    once, before W3a. Without a check inside `--init` itself, a canon that
    already held a colliding pair sailed the SKIP branch straight into W3a
    segpack generation, the same defect the read-side modes (validate-only,
    `--verify-merged`) already close for every OTHER path.

    If `run_init`'s new read-only check were removed, this reproduces the
    reviewer's exact escape: bootstrap a project, hand-write a collision
    into its canon.json, then run the SAME `--init` command the SKIP branch
    issues a second time. Before the fix that second call returned
    immediately with `created: false` and exit 0, never reading `entries{}`
    at all -- this test would see exit 0, not 1, and no error naming either
    key."""
    root = _project(tmp_path)
    seed_canon(root, {FORM_A: _entry(FORM_A, "TargetOne"), FORM_B: _entry(FORM_B, "TargetTwo")})
    before = canon_bytes(root)

    proc = run_canon_init(root)

    assert proc.returncode == 1, f"expected --init to refuse the collision:\n{proc.stdout}\n{proc.stderr}"
    payload = payload_of(proc)
    assert payload["success"] is False, payload
    error = payload["error"]
    assert escaped_key(FORM_A) in error and escaped_key(FORM_B) in error, error
    assert canon_bytes(root) == before, (
        "--init must be a READ on this path -- the create-only write "
        "contract must survive the new collision check"
    )
