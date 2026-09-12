"""tests/segpack_canonized_name_admission.test.py -- #917.

``segpack.py``'s ``strong_names`` filter is a HEURISTIC whose only job is to
guess whether an unknown token is a proper noun or merely a word that happened
to open a sentence (``mid > 0 or multiword``). Applied to a name the canon has
ALREADY decided, that guess does not merely duplicate the decision, it
OVERRULES it: a single-word canonized name whose every occurrence in a segment
is sentence-initial scored ``mid == 0``, failed the filter, and reached neither
``canon_names`` nor ``canon_map`` -- so the translate prompt for that segment
was built without the name's frozen ``canonical_target_form`` and nothing in
the run said so.

The issue that reported this framed it as an uncased-script (Hebrew) problem.
Measurement before the fix refuted that framing: 1 of 308 segments on the
Hebrew book lost a canonized name, against 96 of 359 segments across five
French volumes (``Paris``, ``Jesus``, ``Conrart``, ``Chapelain`` among the
names lost). A French sentence routinely opens with a proper noun, so this
suite uses a FRENCH fixture deliberately -- it exercises the dominant case,
and ``fr.json`` needs no ``name_inventory`` for its candidates to be detected.

What this suite pins, and why each half matters:

  * A canonized name the heuristic would drop is now admitted, and carries its
    frozen target form all the way into ``canon_map``.
  * A canon entry that declares itself NOT identity-bearing -- ``is_proper_name:
    false``, or ``basis: "not_a_name"``, both legal under
    canon-entry.schema.json -- is still dropped. Admitting on bare canon
    MEMBERSHIP would put a common noun into ``canon_map``, where the translate
    task template instructs the translator to render it with that authoritative
    form: a NEW way to ship a wrong name, invented by the fix itself.
  * A canon decision is TERMINAL IN BOTH DIRECTIONS -- the canon's "no" beats
    the heuristic's "yes", not merely its "no". Section 2b covers this, and it
    is a separate property from the one above: while the negative branch fell
    through to the heuristic, a form the canon had explicitly ruled out was
    still admitted whenever it happened to be mid-sentence or multiword, and
    the routing labels every non-None entry canonized. The sentence-initial
    tests in section 2 cannot see that, because there the heuristic rejects
    anyway and they pass either way -- which is how code review, not this
    suite, is what found it. Only a canon value that is not an object at all
    falls through to the heuristic now.
  * A candidate the canon does NOT know is still judged by the heuristic alone.
    This is the assertion that keeps the fix narrow: on the Hebrew book, 82
    inventory-identified forms sit in exactly this position ("introduction",
    "said", "came", Hebrew-numeral chapter labels), and ``mid > 0`` is the only
    thing suppressing them. If this test is missing, nothing stops a later
    widening from flooding every translate prompt with them.
  * Split routing is UNCHANGED (a characterization, so the descope stays
    honest): the ``canon_senses.json`` sidecar is deliberately NOT a second
    admission ground, so a split form the heuristic already admits still lands
    in ``split_names`` and never ``canon_names``, and a split form the
    heuristic rejects stays rejected.
  * The eligibility test ``segpack.py`` applies is DUPLICATED from
    ``occurrence_targets.py::entry_is_index_eligible()`` -- its owning
    definition -- rather than imported, because ``occurrence_targets.py`` is in
    no cache bundle and importing it would make ``segpack.py``'s derivation
    output depend on a file no hash covers. The last test in this file is what
    makes that duplication safe: it runs BOTH predicates over every combination
    of the two fields and asserts they agree, the same construction as
    ``name_discovery.test.py``'s own "disagrees with owner" guard.

Every behavioural test here drives the REAL shipped script end to end -- the
shared ``tests/_canon_project_fixture.py`` stages a Step-0a-shaped durable_root
and ``run_segpack()`` invokes the script as a subprocess. No test hand-builds a
segpack, and none monkeypatches the predicate: a hand-built pack would prove
nothing about whether the shipped CLI actually delivers the target form.
"""
import importlib.util
import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _canon_project_fixture import (  # noqa: E402
    LANGUAGES_SRC,
    SCHEMAS_SRC,
    SCRIPTS_SRC,
    accepted_item,
    make_project,
    run_canon_init,
    run_canon_validate,
    run_segpack,
    write_fragment,
)


def _load_module(name: str, path: Path):
    """In-process load of a real shipped script from its own location, with
    SCRIPTS_SRC temporarily on sys.path -- both scripts loaded below do sibling
    imports that only resolve that way (the same idiom
    ``glossary_disabled_real_candidate_bootstrap.test.py`` and
    ``canon_map_delivery.test.py`` already use). Used ONLY for the two
    predicate-level tests at the end of this file; every behavioural test
    drives the script as a subprocess instead."""
    sys.path.insert(0, str(SCRIPTS_SRC))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS_SRC))


bootstrap_names = _load_module(
    "bootstrap_names_admission_under_test", SCRIPTS_SRC / "bootstrap_names.py"
)
segpack = _load_module("segpack_admission_under_test", SCRIPTS_SRC / "segpack.py")
occurrence_targets = _load_module(
    "occurrence_targets_admission_under_test", SCRIPTS_SRC / "occurrence_targets.py"
)

FRENCH_PARTICLE_CONFIG = "fr.json"

# Source-text fixture data (the book being translated), not code.
#
# CANONIZED_NAME opens its block and appears nowhere else, so the real
# extractor scores it mid == 0, multiword == False -- exactly the shape the
# heuristic drops. HEURISTIC_ONLY_NAME sits in the identical position and is
# never canonized: it is the control that proves the heuristic keeps its full
# authority over candidates the canon does not know. SPLIT_NAME is multiword,
# so the heuristic admits it on its own and the split test can characterize
# ROUTING without also changing admission.
#
# Every one of these three shapes is re-derived from the real extractor by
# test_fixture_premise_* below rather than trusted from this comment.
CANONIZED_NAME = "Conrart"
CANONIZED_TARGET_FORM = "Конрар"
HEURISTIC_ONLY_NAME = "Balzac"
SPLIT_NAME = "Marion Delorme"

FRENCH_BLOCK_ONE = "Conrart partit tôt le matin pour la ville la plus proche."
FRENCH_BLOCK_TWO = "Balzac écrivait sans repos jusqu'au petit jour."
FRENCH_BLOCK_THREE = "Marion Delorme descendit lentement vers le quai."


def french_manifest_doc() -> dict:
    return {
        "segments": [
            {
                "seg": "seg01",
                "title_text": "Chapitre un",
                "kind": "body",
                "word_count": 27,
                "block_ids": ["p1", "p2", "p3"],
            }
        ],
        "blocks": {
            "p1": {"id": "p1", "seg": "seg01", "order_index": 0, "plain_text": FRENCH_BLOCK_ONE},
            "p2": {"id": "p2", "seg": "seg01", "order_index": 1, "plain_text": FRENCH_BLOCK_TWO},
            "p3": {"id": "p3", "seg": "seg01", "order_index": 2, "plain_text": FRENCH_BLOCK_THREE},
        },
        "footnotes": [],
        "verse": {"store": []},
        "generation_hashes": {
            "source_extraction_hash": "e" * 40,
            "source_input_hash": "f" * 40,
        },
    }


# A SECOND fixture, for the precedence tests only (see section 2b). Here the
# not-a-name form is MID-SENTENCE, so the heuristic would admit it on its own
# and only the canon decision can keep it out. It cannot be folded into the
# manifest above: putting CANONIZED_NAME mid-sentence there would make the
# heuristic admit it unaided and quietly gut the acceptance test in section 1.
PRECEDENCE_NEGATIVE_NAME = "Conrart"
PRECEDENCE_POSITIVE_NAME = "Chapelain"
PRECEDENCE_POSITIVE_TARGET_FORM = "Шаплен"

PRECEDENCE_BLOCK_ONE = "Chapelain partit tôt le matin pour la ville la plus proche."
PRECEDENCE_BLOCK_TWO = "Il vit Conrart demain."
PRECEDENCE_BLOCKS = (PRECEDENCE_BLOCK_ONE, PRECEDENCE_BLOCK_TWO)


def precedence_manifest_doc() -> dict:
    return {
        "segments": [
            {
                "seg": "seg01",
                "title_text": "Chapitre un",
                "kind": "body",
                "word_count": 15,
                "block_ids": ["q1", "q2"],
            }
        ],
        "blocks": {
            "q1": {
                "id": "q1",
                "seg": "seg01",
                "order_index": 0,
                "plain_text": PRECEDENCE_BLOCK_ONE,
            },
            "q2": {
                "id": "q2",
                "seg": "seg01",
                "order_index": 1,
                "plain_text": PRECEDENCE_BLOCK_TWO,
            },
        },
        "footnotes": [],
        "verse": {"store": []},
        "generation_hashes": {
            "source_extraction_hash": "1" * 40,
            "source_input_hash": "2" * 40,
        },
    }


# An ordinary accepted canon item with one or two fields overridden -- the only
# way to reach the two shapes that declare an entry NOT identity-bearing, since
# the fixture's own accepted_item() always sets is_proper_name True and basis
# "transliterated" and is owned by other suites tonight.
#
# The BASE item comes from accepted_item() rather than being spelled out again
# here: a hand-copied intake shape drifts silently the moment the canon intake
# schema gains a required field, and this suite would then be pinning a shape
# the real pipeline no longer produces. Overrides are applied on top, so only
# the deviation under test is visible at the call site.
#
# Every call below passes a NON-EMPTY canonical_target_form on purpose: an
# empty one would be dropped from canon_map by the pre-existing #130 rule and
# the test would pass for the wrong reason.
def _accepted_item_with(source_form: str, target_form: str, **overrides) -> dict:
    return {**accepted_item(source_form, target_form), **overrides}


def _project_with_canon(tmp_path, items, manifest=None):
    """Stages the French project, bootstraps an empty canon and merges `items`
    into it through the REAL merge path -- never by writing canon.json by
    hand, so every entry under test has actually cleared intake validation."""
    root = make_project(
        tmp_path,
        particle_config=FRENCH_PARTICLE_CONFIG,
        manifest=french_manifest_doc() if manifest is None else manifest,
    )
    init = run_canon_init(root)
    assert init.returncode == 0, f"canon bootstrap failed:\n{init.stdout}\n{init.stderr}"
    if items:
        frag = write_fragment(root, items)
        merge = run_canon_validate(root, "--merge-batches", str(frag))
        assert merge.returncode == 0, f"canon merge failed:\n{merge.stdout}\n{merge.stderr}"
    return root


def _pack_for(root) -> dict:
    proc = run_segpack(root, particle_config=FRENCH_PARTICLE_CONFIG)
    assert proc.returncode == 0, f"segpack.py failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads((root / "segments" / "segpack_seg01.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 0. Fixture premise -- re-derived from the real extractor, never assumed.
#    If fr.json or the tokenizer ever changes shape, these fail FIRST and name
#    the reason, instead of the behavioural tests below quietly going vacuous.
# ---------------------------------------------------------------------------


def test_fixture_premise_fr_preset_ships_no_name_inventory():
    """Detection here must come from the Capitalized-run heuristic alone. A
    name_inventory in the preset would be a second, independent route into
    candidacy and would change what the tests below are measuring."""
    preset = json.loads((LANGUAGES_SRC / FRENCH_PARTICLE_CONFIG).read_text(encoding="utf-8"))
    assert "name_inventory" not in preset, (
        f"{FRENCH_PARTICLE_CONFIG} now ships a name_inventory -- this suite's "
        "candidate detection must be re-derived before its results mean "
        "anything about the Capitalized-run heuristic"
    )


def _candidate_stats(texts) -> dict:
    """What the SHIPPED extractor makes of these fixture blocks: name -> freq
    and mid-sentence count. Every claim this suite makes about its own fixture
    text is derived through here, never asserted from a comment -- a fixture
    whose shape has silently drifted would otherwise make the behavioural tests
    below prove something other than what they say."""
    lang = bootstrap_names.load_language_config(
        FRENCH_PARTICLE_CONFIG, languages_dir=LANGUAGES_SRC
    )
    stats = {}
    for text in texts:
        for name, mid_sentence in bootstrap_names.extract_candidates(text, lang):
            row = stats.setdefault(name, {"freq": 0, "mid": 0})
            row["freq"] += 1
            row["mid"] += int(mid_sentence)
    return stats


def _assert_heuristic_would_admit(name: str, texts) -> None:
    """Guards the precedence tests against silently degrading into copies of
    the sentence-initial tests above. Their whole point is the case where the
    canon says NO and the heuristic says YES; if the fixture text ever stopped
    putting `name` mid-sentence, they would still pass while proving nothing."""
    row = _candidate_stats(texts).get(name)
    assert row is not None, (
        f"{name!r} is not even a candidate in this fixture text -- the "
        "precedence tests would be vacuous"
    )
    assert row["mid"] > 0 or len(name.split()) > 1, (
        f"{name!r} must be mid-sentence (or multiword) here, so the HEURISTIC "
        f"admits it and only the canon decision can keep it out; got {row!r}"
    )


def test_fixture_premise_the_names_have_the_shapes_this_suite_needs():
    """The whole suite rests on three claims about the fixture text: the
    canonized name and the control are single-word and NEVER mid-sentence (so
    the heuristic drops both), and the split form is multiword (so the
    heuristic admits it and the split test characterizes routing only). Derived
    by running the shipped extractor over the fixture blocks."""
    stats = _candidate_stats((FRENCH_BLOCK_ONE, FRENCH_BLOCK_TWO, FRENCH_BLOCK_THREE))

    assert set(stats) == {CANONIZED_NAME, HEURISTIC_ONLY_NAME, SPLIT_NAME}, (
        "the fixture text no longer yields exactly the three candidates this "
        f"suite reasons about; got {stats!r}"
    )
    for name in (CANONIZED_NAME, HEURISTIC_ONLY_NAME):
        assert stats[name]["mid"] == 0, (
            f"{name!r} must be sentence-initial in EVERY occurrence, or the "
            f"heuristic would admit it on its own and this suite proves "
            f"nothing; got {stats[name]!r}"
        )
        assert len(name.split()) == 1, f"{name!r} must be single-word; got {name!r}"
    assert len(SPLIT_NAME.split()) > 1, (
        f"{SPLIT_NAME!r} must be multiword so the heuristic admits it unaided"
    )


# ---------------------------------------------------------------------------
# 1. The acceptance criterion -- a canonized name the heuristic drops now
#    reaches names, canon_names AND canon_map with its frozen target form.
# ---------------------------------------------------------------------------


def test_canonized_sentence_initial_name_reaches_names_canon_names_and_canon_map(tmp_path):
    """#917's requested outcome, end to end through the shipped CLI: a
    single-word canonized name that is sentence-initial in every occurrence is
    admitted despite mid == 0, and the frozen canonical_target_form actually
    arrives in canon_map -- which is the field the translate prompt reads."""
    root = _project_with_canon(
        tmp_path, [accepted_item(CANONIZED_NAME, CANONIZED_TARGET_FORM)]
    )

    pack = _pack_for(root)

    assert CANONIZED_NAME in pack["names"], (
        "a canonized name must not be withheld by a heuristic whose only job "
        "is to guess at names the canon has not decided; got names="
        f"{pack['names']!r}"
    )
    assert CANONIZED_NAME in pack["canon_names"], pack["canon_names"]
    assert pack["canon_map"].get(CANONIZED_NAME) == CANONIZED_TARGET_FORM, (
        "the frozen target form must reach canon_map -- without it the "
        "translate prompt for this segment is built as if the name had never "
        f"been canonized; got canon_map={pack['canon_map']!r}"
    )
    assert CANONIZED_NAME not in pack["new_names"], pack["new_names"]


# ---------------------------------------------------------------------------
# 2. Canon MEMBERSHIP is not enough -- an entry that declares itself not
#    identity-bearing stays out of all three fields.
# ---------------------------------------------------------------------------


def test_entry_with_is_proper_name_false_stays_out_of_all_three(tmp_path):
    """A canon entry may legally say `is_proper_name: false`. Admitting it
    would put a common noun into canon_map, where the translate task template
    tells the translator to render it with that authoritative form -- a new way
    to ship a wrong name, invented by the fix. It must stay out."""
    root = _project_with_canon(
        tmp_path,
        [
            _accepted_item_with(
                CANONIZED_NAME, CANONIZED_TARGET_FORM, is_proper_name=False
            )
        ],
    )

    pack = _pack_for(root)

    assert CANONIZED_NAME not in pack["names"], pack["names"]
    assert CANONIZED_NAME not in pack["canon_names"], pack["canon_names"]
    assert CANONIZED_NAME not in pack["canon_map"], pack["canon_map"]


def test_entry_with_basis_not_a_name_stays_out_of_all_three(tmp_path):
    """The second, independent way a canon entry declares itself not
    identity-bearing. `is_proper_name` is left TRUE here on purpose, so this
    test isolates the `basis` clause -- were the predicate to test only
    `is_proper_name`, the sibling test above would still pass and this one
    would go red."""
    root = _project_with_canon(
        tmp_path,
        [
            _accepted_item_with(
                CANONIZED_NAME, CANONIZED_TARGET_FORM, basis="not_a_name"
            )
        ],
    )

    pack = _pack_for(root)

    assert CANONIZED_NAME not in pack["names"], pack["names"]
    assert CANONIZED_NAME not in pack["canon_names"], pack["canon_names"]
    assert CANONIZED_NAME not in pack["canon_map"], pack["canon_map"]


# ---------------------------------------------------------------------------
# 2b. PRECEDENCE -- a canon decision is terminal in BOTH directions.
#
#     The two tests above put the not-a-name entry on a SENTENCE-INITIAL form,
#     where the heuristic rejects it anyway, so they pass whether or not the
#     canon's "no" is actually respected. Code review caught that gap: while
#     the negative branch fell through to the heuristic, a form the canon had
#     explicitly ruled out was still admitted whenever it happened to be
#     mid-sentence or multiword -- and the routing below labels EVERY non-None
#     entry canonized and emits its target form into canon_map, so that form
#     reached the translator as an authoritative rendering instruction. These
#     two tests are the ones that fail if that fall-through ever comes back.
#
#     Making the decision terminal costs nothing that ships today: measured
#     across every durable root on this machine, 2 087 canon entries over six
#     books, the number carrying `is_proper_name: false` or
#     `basis: "not_a_name"` is ZERO.
# ---------------------------------------------------------------------------


def test_is_proper_name_false_beats_the_heuristic_on_a_mid_sentence_form(tmp_path):
    """The canon says no, the heuristic says yes -- the canon wins. Without
    this, an entry declaring `is_proper_name: false` on a mid-sentence form was
    admitted, labelled canonized, and had its target form delivered to the
    translator as authoritative."""
    _assert_heuristic_would_admit(PRECEDENCE_NEGATIVE_NAME, PRECEDENCE_BLOCKS)
    root = _project_with_canon(
        tmp_path,
        [
            _accepted_item_with(
                PRECEDENCE_NEGATIVE_NAME, CANONIZED_TARGET_FORM, is_proper_name=False
            ),
            accepted_item(PRECEDENCE_POSITIVE_NAME, PRECEDENCE_POSITIVE_TARGET_FORM),
        ],
        manifest=precedence_manifest_doc(),
    )

    pack = _pack_for(root)

    assert PRECEDENCE_NEGATIVE_NAME not in pack["names"], (
        "a canon entry that declares itself NOT a name must lose to nothing -- "
        "least of all to the heuristic it overrules; got names="
        f"{pack['names']!r}"
    )
    assert PRECEDENCE_NEGATIVE_NAME not in pack["canon_names"], pack["canon_names"]
    assert PRECEDENCE_NEGATIVE_NAME not in pack["canon_map"], (
        "this is the consequence that matters: canon_map is what the translate "
        f"prompt renders as authoritative; got {pack['canon_map']!r}"
    )
    # Not a vacuous "nothing was admitted" pass: the eligible canonized name in
    # the SAME pack still arrives with its frozen form.
    assert pack["canon_map"].get(PRECEDENCE_POSITIVE_NAME) == PRECEDENCE_POSITIVE_TARGET_FORM, (
        pack["canon_map"]
    )


def test_basis_not_a_name_beats_the_heuristic_on_a_mid_sentence_form(tmp_path):
    """The same precedence property through the OTHER declaration. As in
    section 2, `is_proper_name` is left TRUE so only the `basis` clause can
    decide the outcome."""
    _assert_heuristic_would_admit(PRECEDENCE_NEGATIVE_NAME, PRECEDENCE_BLOCKS)
    root = _project_with_canon(
        tmp_path,
        [
            _accepted_item_with(
                PRECEDENCE_NEGATIVE_NAME, CANONIZED_TARGET_FORM, basis="not_a_name"
            ),
            accepted_item(PRECEDENCE_POSITIVE_NAME, PRECEDENCE_POSITIVE_TARGET_FORM),
        ],
        manifest=precedence_manifest_doc(),
    )

    pack = _pack_for(root)

    assert PRECEDENCE_NEGATIVE_NAME not in pack["names"], pack["names"]
    assert PRECEDENCE_NEGATIVE_NAME not in pack["canon_names"], pack["canon_names"]
    assert PRECEDENCE_NEGATIVE_NAME not in pack["canon_map"], pack["canon_map"]
    assert pack["canon_map"].get(PRECEDENCE_POSITIVE_NAME) == PRECEDENCE_POSITIVE_TARGET_FORM, (
        pack["canon_map"]
    )


# ---------------------------------------------------------------------------
# 3. The narrowness of the fix -- the heuristic keeps full authority over a
#    candidate the canon does not know. This is the test that keeps 82 measured
#    junk inventory forms out of every translate prompt.
# ---------------------------------------------------------------------------


def test_non_canonized_sentence_initial_candidate_is_still_dropped(tmp_path):
    """The control, in the SAME pack as the admitted name above: identical
    position, identical shape, no canon entry -- and therefore still dropped.

    Measured on the live Hebrew book, 82 forms sit in exactly this position
    ("introduction", "said", "came", "blessed", Hebrew-numeral chapter labels)
    and `mid > 0` is the only thing suppressing them. Admitting them was the
    remedy the issue proposed; it would have pushed all 82 into the translator's
    prompt and forced a third of the book to re-translate to deliver one name.
    If this assertion is ever deleted, that regression can land silently."""
    root = _project_with_canon(
        tmp_path, [accepted_item(CANONIZED_NAME, CANONIZED_TARGET_FORM)]
    )

    pack = _pack_for(root)

    assert HEURISTIC_ONLY_NAME not in pack["names"], (
        "a candidate the canon has NOT decided must still be judged by the "
        "heuristic alone -- there is nothing else to suppress a false positive "
        f"with; got names={pack['names']!r}"
    )
    assert HEURISTIC_ONLY_NAME not in pack["canon_names"], pack["canon_names"]
    assert HEURISTIC_ONLY_NAME not in pack["new_names"], pack["new_names"]
    # The admitted name is in the SAME pack, so this is not a vacuous "nothing
    # was admitted at all" pass.
    assert CANONIZED_NAME in pack["names"], pack["names"]


# ---------------------------------------------------------------------------
# 4. Split routing, characterized -- the sense sidecar is NOT a second
#    admission ground, and admitted splits still bypass canon_names.
# ---------------------------------------------------------------------------


def _senses_doc(*source_forms: str) -> dict:
    """A minimal schema-valid homonym sidecar. The sidecar is read through the
    shipped loader, which schema-validates it; the evidence offsets are never
    re-verified at this stage, so plausible placeholders are enough."""
    return {
        "schema_version": 1,
        "entries_by_source_form": {
            source_form: {
                "senses": [
                    {
                        "sense_id": f"{source_form}-1",
                        "disambiguator": "premier référent",
                        "index_scope": "narrative",
                        "evidence": {
                            "block": "p3",
                            "seg": "seg01",
                            "char_start": 0,
                            "char_end": len(source_form),
                            "context_start": 0,
                            "context_end": len(FRENCH_BLOCK_THREE),
                            "sha256": "0" * 64,
                        },
                    },
                    {
                        "sense_id": f"{source_form}-2",
                        "disambiguator": "second référent",
                        "index_scope": "allusion",
                        "evidence": {
                            "block": "p3",
                            "seg": "seg01",
                            "char_start": 0,
                            "char_end": len(source_form),
                            "context_start": 0,
                            "context_end": len(FRENCH_BLOCK_THREE),
                            "sha256": "0" * 64,
                        },
                    },
                ]
            }
            for source_form in source_forms
        },
    }


def test_split_routing_is_unchanged(tmp_path):
    """Characterization, so the deliberate descope stays honest. A split form
    the heuristic ALREADY admits (multiword) still routes to split_names and
    never to canon_names or canon_map -- the split branch runs before the canon
    lookup and this change did not move it. And a split form the heuristic
    REJECTS stays rejected: the sidecar was deliberately NOT made a second
    admission ground, because a split carries no frozen target form by design,
    while split_names is inside used_terms_hash and would buy a content-bearing
    re-translation for nothing."""
    root = _project_with_canon(
        tmp_path, [accepted_item(CANONIZED_NAME, CANONIZED_TARGET_FORM)]
    )
    (root / "canon_senses.json").write_text(
        json.dumps(_senses_doc(SPLIT_NAME, HEURISTIC_ONLY_NAME), ensure_ascii=False),
        encoding="utf-8",
    )

    pack = _pack_for(root)

    assert SPLIT_NAME in pack["split_names"], (
        f"the admitted split form must route to split_names; got {pack['split_names']!r}"
    )
    assert [s["sense_id"] for s in pack["split_names"][SPLIT_NAME]] == [
        f"{SPLIT_NAME}-1",
        f"{SPLIT_NAME}-2",
    ], pack["split_names"][SPLIT_NAME]
    assert SPLIT_NAME not in pack["canon_names"], pack["canon_names"]
    assert SPLIT_NAME not in pack["canon_map"], pack["canon_map"]
    assert SPLIT_NAME not in pack["new_names"], pack["new_names"]

    assert HEURISTIC_ONLY_NAME not in pack["split_names"], (
        "a split form the heuristic rejects must stay rejected -- the sense "
        "sidecar is not an admission ground; got "
        f"{pack['split_names']!r}"
    )
    assert HEURISTIC_ONLY_NAME not in pack["names"], pack["names"]
    # The canonized name still arrives, so a sidecar's presence did not simply
    # suppress everything.
    assert pack["canon_map"].get(CANONIZED_NAME) == CANONIZED_TARGET_FORM, pack["canon_map"]


# ---------------------------------------------------------------------------
# 5. Drift guard for the DELIBERATE duplication -- segpack.py spells the
#    eligibility test itself rather than importing occurrence_targets.py,
#    because that module is in no cache bundle and importing it would make
#    segpack.py's derivation output depend on a file no hash covers. What
#    makes that safe is this matrix, not a comment.
# ---------------------------------------------------------------------------

# A name_stats row the heuristic REJECTS on its own (mid == 0, single word,
# length > 1). Feeding this shape isolates the canon-entry half of segpack.py's
# predicate: with it, the predicate returns True if and only if the canon entry
# is eligible.
HEURISTIC_REJECTS = {"freq": 1, "mid": 0, "multiword": False}
HEURISTIC_ADMITS = {"freq": 1, "mid": 1, "multiword": False}


def _basis_enum() -> list:
    """Read from the shipped schema, never hand-typed here -- a hand-typed list
    inside a drift test freezes exactly what the test exists to detect."""
    schema = json.loads(
        (SCHEMAS_SRC / "canon-entry.schema.json").read_text(encoding="utf-8")
    )
    values = schema["properties"]["basis"]["enum"]
    assert isinstance(values, list) and len(values) >= 4, (
        f"canon-entry.schema.json's basis enum parsed implausibly: {values!r}"
    )
    return values


def test_segpack_eligibility_agrees_with_its_owning_definition_whatever_the_heuristic_says():
    """`occurrence_targets.entry_is_index_eligible()` is the ONE predicate every
    category-based inclusion in the Mentions universe goes through, and it owns
    this rule. segpack.py duplicates it on purpose. Both are run here over
    EVERY combination of is_proper_name (true / false / absent) x basis (each
    schema enum value / absent), against BOTH heuristic verdicts.

    Running both stats rows is what makes this a PRECEDENCE test rather than a
    mere agreement test. Over a heuristic-REJECTS row, an implementation whose
    negative branch fell through to the heuristic would agree here by accident,
    because both sides end up False for different reasons. Over a
    heuristic-ADMITS row the two answers separate, and the equality asserted
    below is exactly the statement "a canon decision is terminal in both
    directions". That is the property code review found missing.

    The combination count is asserted, and so is the fact that both outcomes
    occur within EACH half -- a matrix that iterated zero times, or that only
    ever saw one answer on one side, would otherwise print exactly what a real
    pass prints."""
    basis_values = _basis_enum()
    basis_options = [*basis_values, None]  # None == the field is absent
    name_options = [True, False, None]  # None == the field is absent
    stats_rows = {"heuristic-rejects": HEURISTIC_REJECTS, "heuristic-admits": HEURISTIC_ADMITS}
    expected_combinations = len(name_options) * len(basis_options) * len(stats_rows)
    assert expected_combinations == 3 * (len(basis_values) + 1) * 2

    checked = 0
    outcomes = {label: set() for label in stats_rows}
    for label, stats in stats_rows.items():
        for is_proper_name in name_options:
            for basis in basis_options:
                entry = {"source_form": CANONIZED_NAME}
                if is_proper_name is not None:
                    entry["is_proper_name"] = is_proper_name
                if basis is not None:
                    entry["basis"] = basis

                owner = occurrence_targets.entry_is_index_eligible(entry)
                mine = segpack._is_strong_candidate(
                    CANONIZED_NAME, stats, {CANONIZED_NAME: entry}
                )
                assert mine is owner, (
                    "segpack.py's duplicated eligibility test disagrees with "
                    "its owning definition "
                    f"(occurrence_targets.entry_is_index_eligible) on the "
                    f"{label} row for entry={entry!r}: segpack={mine!r}, "
                    f"owner={owner!r}. A canon decision must be terminal in "
                    "BOTH directions -- the heuristic never gets to overrule it"
                )
                outcomes[label].add(owner)
                checked += 1

    assert checked == expected_combinations, (
        f"the matrix ran {checked} times, expected {expected_combinations}"
    )
    for label, seen in outcomes.items():
        assert seen == {True, False}, (
            f"the {label} half never produced both answers, so agreement there "
            f"proves nothing; got {seen!r}"
        )
    print(f"checked={checked} entry shapes x heuristic verdicts against the owning predicate")


def test_segpack_falls_back_to_the_heuristic_only_when_there_is_no_canon_DECISION():
    """The other half of the predicate, without which the agreement test above
    could be satisfied by a predicate that simply returned True for everything:
    with no canon entry at all, admission is decided by the heuristic alone,
    in both directions.

    The non-dict cases below are the deliberately UNCHANGED path, pinned so the
    new terminal branch cannot be over-tightened later without something going
    red: a canon value that is not an object is not a decision, so it falls
    through to the heuristic rather than being read either as "present,
    therefore a name" or as "not eligible, therefore excluded"."""
    assert segpack._is_strong_candidate(CANONIZED_NAME, HEURISTIC_REJECTS, {}) is False
    assert segpack._is_strong_candidate(CANONIZED_NAME, HEURISTIC_ADMITS, {}) is True

    for not_a_decision in (None, "Конрар", ["Конрар"], 7):
        entries = {CANONIZED_NAME: not_a_decision}
        assert (
            segpack._is_strong_candidate(CANONIZED_NAME, HEURISTIC_ADMITS, entries) is True
        ), (
            f"a non-dict canon value ({not_a_decision!r}) is not a canon "
            "DECISION -- the heuristic must still be able to admit the name"
        )
        assert (
            segpack._is_strong_candidate(CANONIZED_NAME, HEURISTIC_REJECTS, entries) is False
        ), (
            f"a non-dict canon value ({not_a_decision!r}) must not admit a name "
            "the heuristic rejects"
        )


def test_the_canon_branch_does_not_apply_the_heuristics_length_floor():
    """The canon branch deliberately does NOT apply the heuristic's
    `len(name) != 1` floor, and this test exists to stop one specific edit.

    WHY THE ASYMMETRY IS CORRECT. The length floor is part of the GUESS: a
    lone character is weak evidence of a proper noun, so the heuristic refuses
    it. A one-character source form the canon has ruled on is not evidence at
    all, it is a decision -- someone recorded a frozen target form for it --
    and the whole point of #917 is that a decision is never re-litigated by the
    heuristic that exists to guess in its absence.

    WHAT THIS TEST IS GUARDING AGAINST, specifically. A later reader will notice
    that the canon branch lacks the length check sitting a few lines below it
    and reach for the obvious tidy-up: hoist `len(name) != 1` so both branches
    share it. That edit silently re-breaks #917 for every single-character
    canonized form -- the same defect class this release exists to close,
    reintroduced by someone reading the file carefully. Nothing else in this
    suite would go red, because every other name here is multi-character. If
    you are reading this because you tripped it: the floor belongs to the
    heuristic only; do not lift it.

    Measured on the corpus at the time of writing -- 2 087 canon entries over
    six books -- single-character source forms: 0. So this pin protects the
    CODE from a future edit; it repairs nothing observable today, and no
    release note should claim otherwise.
    """
    single_character_name = "Ж"
    decided = {
        "source_form": single_character_name,
        "is_proper_name": True,
        "basis": "transliterated",
    }

    assert (
        segpack._is_strong_candidate(
            single_character_name, HEURISTIC_REJECTS, {single_character_name: decided}
        )
        is True
    ), (
        "a one-character form the canon has decided must be admitted -- the "
        "heuristic's length floor does not apply to a canon decision"
    )

    # The floor must still EXIST inside the heuristic, or the assertion above
    # could be satisfied by deleting it outright rather than by keeping the two
    # branches properly separate.
    assert (
        segpack._is_strong_candidate(single_character_name, HEURISTIC_ADMITS, {}) is False
    ), (
        "the length floor still governs a name the canon has NOT decided -- a "
        "lone character is weak evidence of a proper noun and the heuristic "
        "must keep refusing it"
    )

    # And the canon's "no" stays terminal at one character too.
    ruled_out = {**decided, "basis": "not_a_name"}
    assert (
        segpack._is_strong_candidate(
            single_character_name, HEURISTIC_ADMITS, {single_character_name: ruled_out}
        )
        is False
    )
