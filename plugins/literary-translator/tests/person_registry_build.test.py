"""tests/person_registry_build.test.py -- `person_registry.py --claims` and
`--build`, the gated half of W9r (#550).

What these gates defend is narrow and worth stating: the artifact is read by a
human doing genealogy and by whatever tool imports it, and the two failures
that matter are a FABRICATED PERSON (two distinct people merged into one
record) and a FABRICATED KINSHIP EDGE (a relation the book never states). Both
are silent -- the file looks the same either way -- so every gate below exists
to make one of them loud.

The division of labour these tests pin:

  * deterministic, checked here against disk -- coverage, invented units, that
    a quote EXISTS in the container its locator names, that a verdict is bound
    to the prep it judged, that an adjudication set joins one-to-one, and every
    number in the output;
  * adjudicated, checked by the second model pass -- whether a quote SAYS what
    the claim says, and whether forms denote one person.

A quote check can never do the adjudicator's job: a model can cite a real
sentence and attach an unrelated typed claim to it, and
`test_quote_that_exists_but_is_unaffirmed_is_refuted` is the pin that the
design does not confuse the two.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _registry_fixture as fx  # noqa: E402


def _load_module():
    spec = importlib.util.spec_from_file_location("person_registry_build_under_test", fx.SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pr = _load_module()


@pytest.fixture()
def prepped(tmp_path):
    root = fx.build_root(tmp_path)
    code, payload = fx.run(root, "--prep")
    assert code == 0, payload
    return root


def _through_claims(root, verdict=None):
    fx.write_verdict(root, verdict if verdict is not None else fx.verdict_doc(root))
    return fx.run(root, "--claims")


def _full(root, *, refuse=()):
    code, payload = _through_claims(root)
    assert code == 0, payload
    fx.write_adjudications(root, refuse_person_ids=refuse)
    return fx.run(root, "--build")


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------

def test_build_emits_a_schema_valid_registry(prepped):
    code, payload = _full(prepped)
    assert code == 0, payload
    reg = fx.registry(prepped)
    assert reg["provenance"]["assembly_currency"] == "not_bound"
    assert (prepped / "registry" / "PEOPLE.md").is_file()
    assert {p["person_id"] for p in reg["people"]} == {"john", "paul", "mary", "valjean"}


def test_both_emitted_artifacts_are_byte_identical_on_a_re_run(prepped):
    """BOTH files, as bytes. The registry carries no timestamp on purpose, and
    the whole diff-acceptance discipline elsewhere in this plugin depends on a
    re-run over unchanged inputs producing the same bytes -- so a set iterated
    without a sort, anywhere in the build, has to show up here."""
    code, _ = _full(prepped)
    assert code == 0
    emitted = ["person_registry.json", "PEOPLE.md"]
    first = {name: (prepped / "registry" / name).read_bytes() for name in emitted}
    assert all(first.values())
    assert fx.run(prepped, "--claims")[0] == 0
    assert fx.run(prepped, "--build")[0] == 0
    for name in emitted:
        assert (prepped / "registry" / name).read_bytes() == first[name], name


# ---------------------------------------------------------------------------
# P2-P5: the pre-claims gates
# ---------------------------------------------------------------------------

def test_stale_verdict_is_refused(prepped):
    verdict = fx.verdict_doc(prepped)
    verdict["input_sha256"] = "0" * 64
    code, payload = _through_claims(prepped, verdict)
    assert code == 1
    assert payload["reason"] == "verdicts_stale"


def test_unit_claimed_by_nobody_is_refused(prepped):
    verdict = fx.verdict_doc(prepped)
    verdict["refusals"] = []          # Bernard now appears nowhere
    code, payload = _through_claims(prepped, verdict)
    assert code == 1
    assert payload["reason"] == "coverage_violation"
    assert "Bernard" in payload["error"]


def test_unit_claimed_twice_is_refused(prepped):
    """A merge conflict. Two people cannot both own one form -- that is a
    fabricated person by construction."""
    verdict = fx.verdict_doc(prepped)
    verdict["people"][1]["units"].append({"source_form": "Jean", "sense_id": None})
    code, payload = _through_claims(prepped, verdict)
    assert code == 1
    assert payload["reason"] == "coverage_violation"


def test_invented_unit_is_refused(prepped):
    verdict = fx.verdict_doc(prepped)
    verdict["people"][1]["units"] = [{"source_form": "Napoléon", "sense_id": None}]
    code, payload = _through_claims(prepped, verdict)
    assert code == 1
    assert payload["reason"] == "invented_units"


def test_review_queue_form_may_not_become_a_person(prepped):
    """The project's own canon records this form as unresolved. A pass that
    resolved it into a person would be answering the project's open question
    with a model's guess -- and bucket-agnostic coverage alone would allow it,
    which is why P3 carries a bucket constraint."""
    verdict = fx.verdict_doc(prepped)
    verdict["refusals"] = []
    verdict["people"][1]["units"].append({"source_form": "Bernard", "sense_id": None})
    code, payload = _through_claims(prepped, verdict)
    assert code == 1
    assert payload["reason"] == "refusal_only_misplaced"


def test_quote_absent_from_its_container_is_refused(prepped):
    verdict = fx.verdict_doc(prepped)
    verdict["people"][0]["relations"][0]["evidence"]["quote"] = "Jean n'a jamais existé"
    code, payload = _through_claims(prepped, verdict)
    assert code == 1
    assert payload["reason"] == "quote_not_in_container"


def test_verse_quote_resolves_against_the_verse_not_its_parent_block(prepped):
    """The origin-aware locator, both directions. The verse's prose lives in
    `manifest.verse.store[]`; its parent block carries only the placeholder.
    Cited correctly it verifies; cited against the parent block -- which a
    block-only locator would have forced -- it does not."""
    good = fx.verdict_doc(prepped)
    good["people"][0]["relations"].append(
        {"type": "brother_of", "to_unregistered": "Marie",
         "evidence": {"quote": "frère de Marie", "locator": dict(fx.LOC_VERSE)}})
    code, payload = _through_claims(prepped, good)
    assert code == 0, payload

    bad = fx.verdict_doc(prepped)
    bad["people"][0]["relations"].append(
        {"type": "brother_of", "to_unregistered": "Marie",
         "evidence": {"quote": "frère de Marie", "locator": dict(fx.LOC_2)}})
    code, payload = _through_claims(prepped, bad)
    assert code == 1
    assert payload["reason"] == "quote_not_in_container"


def test_unresolvable_verse_locator_is_refused(prepped):
    verdict = fx.verdict_doc(prepped)
    verdict["people"][0]["relations"].append(
        {"type": "brother_of", "to_unregistered": "Marie",
         "evidence": {"quote": "frère de Marie",
                      "locator": {"origin": "embedded_verse", "vid": "V999"}}})
    code, payload = _through_claims(prepped, verdict)
    assert code == 1
    assert payload["reason"] == "locator_unresolved"


def test_surface_containing_a_sentinel_is_refused(prepped):
    """A sentinel is replaced by the renderer and is never a printed name."""
    verdict = fx.verdict_doc(prepped)
    verdict["people"][0]["printed_surfaces"].append("⟦FNREF_1⟧")
    code, payload = _through_claims(prepped, verdict)
    assert code == 1
    assert payload["reason"] == "surface_contains_sentinel"


def test_dangling_relation_target_is_refused(prepped):
    verdict = fx.verdict_doc(prepped)
    verdict["people"][0]["relations"][0]["to_person_id"] = "nobody"
    code, payload = _through_claims(prepped, verdict)
    assert code == 1
    assert payload["reason"] == "dangling_relation_target"


# ---------------------------------------------------------------------------
# B1-B2: binding an adjudication set to the claims it judged
# ---------------------------------------------------------------------------

def test_adjudications_for_a_different_claims_document_are_refused(prepped):
    assert _through_claims(prepped)[0] == 0
    doc = fx.write_adjudications(prepped)
    doc["claims_sha256"] = "0" * 64
    (prepped / "registry" / "registry_adjudications.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    code, payload = fx.run(prepped, "--build")
    assert code == 1
    assert payload["reason"] == "adjudications_stale"


@pytest.mark.parametrize("mutation,reason", [
    ("drop", "adjudication_missing"),
    ("duplicate", "adjudication_duplicate"),
    ("invent", "adjudication_invented"),
])
def test_adjudication_join_must_be_exactly_one_to_one(prepped, mutation, reason):
    """An anonymous positional list could apply an affirmation meant for a safe
    claim to an unsafe one, and nothing downstream would see it. So the join is
    on claim_id, and every deviation is a hard failure."""
    assert _through_claims(prepped)[0] == 0
    doc = fx.write_adjudications(prepped)
    if mutation == "drop":
        doc["adjudications"].pop()
    elif mutation == "duplicate":
        doc["adjudications"].append(dict(doc["adjudications"][0]))
    else:
        row = dict(doc["adjudications"][0])
        row["claim_id"] = "f" * 64
        doc["adjudications"].append(row)
    (prepped / "registry" / "registry_adjudications.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    code, payload = fx.run(prepped, "--build")
    assert code == 1
    assert payload["reason"] == reason


# ---------------------------------------------------------------------------
# B3: what an unaffirmed judgement does
# ---------------------------------------------------------------------------

def test_unaffirmed_person_refuses_every_unit_and_emits_no_person(prepped):
    """Refusal, never a split into single-unit survivors: a survivor was never
    itself adjudicated, so emitting one would put back exactly the
    unadjudicated person record the claim existed to prevent."""
    code, payload = _full(prepped, refuse=("valjean",))
    assert code == 0, payload
    reg = fx.registry(prepped)
    assert "valjean" not in {p["person_id"] for p in reg["people"]}
    refused = {(r["unit"]["source_form"], r["unit"]["sense_id"]): r for r in reg["refusals"]}
    assert ("Jean Valjean", "convict") in refused
    assert ("Jean Valjean", "mayor") in refused
    assert refused[("Jean Valjean", "convict")]["refused_by"] == "adjudication"


def test_claims_owned_by_an_unaffirmed_person_are_cascade_refuted(prepped):
    code, _ = _full(prepped, refuse=("john",))
    assert code == 0
    reg = fx.registry(prepped)
    cascaded = [c for c in reg["refuted_claims"]
                if c["person_id"] == "john" and c["reason"] == "owner_identity_not_affirmed"]
    # john carried an identity_status claim, a printed surface, a relation, a
    # place and a date -- every one of them loses its owner.
    assert {c["kind"] for c in cascaded} >= {"identity_status", "printed_surface", "relation", "place", "date"}
    assert "john" not in {p["person_id"] for p in reg["people"]}


def test_unaffirmed_confirmed_status_falls_back_to_contested(prepped):
    """`confirmed` asserts an identity is settled, which is a claim about the
    book. Unaffirmed, the person survives -- but as contested."""
    assert _through_claims(prepped)[0] == 0
    claims = json.loads((prepped / "registry" / "registry_claims.json").read_text(encoding="utf-8"))
    rows = []
    for claim in claims["claims"]:
        deny = claim["kind"] == "identity_status" and claim["person_id"] == "john"
        rows.append({"claim_id": claim["claim_id"], "affirmed": not deny,
                     "reason": "the identity is still open" if deny else "stated"})
    (prepped / "registry" / "registry_adjudications.json").write_text(
        json.dumps({"schema_version": 1, "input_sha256": claims["input_sha256"],
                    "claims_sha256": claims["claims_sha256"], "adjudications": rows},
                   ensure_ascii=False), encoding="utf-8")
    code, _ = fx.run(prepped, "--build")
    assert code == 0
    john = next(p for p in fx.registry(prepped)["people"] if p["person_id"] == "john")
    assert john["identity_status"] == "contested"
    # A DETERMINISTIC sentence in the live field, and the adjudicator's own
    # words kept where they belong. Pass B's reason is a refutation that
    # nothing affirmed, so publishing it as the registry's explanation would
    # put unchecked prose in the very field the claim protects.
    assert john["identity_status_reason"] == (
        "the adjudication pass did not affirm the stated identity status; "
        "see refuted_claims[] for its reason"
    )
    assert any(r["kind"] == "identity_status" and r["reason"] == "the identity is still open"
               for r in fx.registry(prepped)["refuted_claims"])


def test_quote_that_exists_but_is_unaffirmed_is_refuted(prepped):
    """The whole reason a second pass exists. The quote is real and P5 accepts
    it; only a reader can decide it does not state the claim, and when it does
    not, the relation must leave the person and be recorded."""
    assert _through_claims(prepped)[0] == 0
    claims = json.loads((prepped / "registry" / "registry_claims.json").read_text(encoding="utf-8"))
    rows = []
    for claim in claims["claims"]:
        deny = claim["kind"] == "relation" and claim["person_id"] == "john"
        rows.append({"claim_id": claim["claim_id"], "affirmed": not deny,
                     "reason": "the sentence mentions both but states no relation" if deny else "stated"})
    (prepped / "registry" / "registry_adjudications.json").write_text(
        json.dumps({"schema_version": 1, "input_sha256": claims["input_sha256"],
                    "claims_sha256": claims["claims_sha256"], "adjudications": rows},
                   ensure_ascii=False), encoding="utf-8")
    code, _ = fx.run(prepped, "--build")
    assert code == 0
    reg = fx.registry(prepped)
    john = next(p for p in reg["people"] if p["person_id"] == "john")
    assert john["relations"] == []
    assert any(c["kind"] == "relation" and c["person_id"] == "john" for c in reg["refuted_claims"])


# ---------------------------------------------------------------------------
# B4: every number is the plugin's own
# ---------------------------------------------------------------------------

def test_printed_form_counts_are_computed_and_word_bounded(prepped):
    """`John` is printed six times, and the count is built from the DELIVERED
    corpus, not from node text alone.

    Two nodes (three occurrences: `John was`, `John's`, `John came`), one
    footnote, and BOTH halves of the embedded verse -- a rendered verse and its
    literal gloss are both printed when the policy emits both, so a name in each
    is printed twice. `Johnson` is none of them: the same boundary rule the
    shipped wikilinker uses.
    """
    assert _full(prepped)[0] == 0
    prose = fx.NODE_1 + fx.NODE_2 + fx.FOOTNOTE_TEXT
    assert prose.count("John") == 5          # one of them is `Johnson`
    assert fx.VERSE_RENDERED.count("John") == 1 and fx.VERSE_GLOSS.count("John") == 1
    john = next(p for p in fx.registry(prepped)["people"] if p["person_id"] == "john")
    printed = {row["surface"]: row for row in john["printed_forms"]}
    assert printed["John"]["status"] == "counted"
    assert printed["John"]["count"] == 6      # 4 - Johnson + rendered + gloss


def test_mention_count_is_null_with_a_reason_when_nothing_is_attributable(prepped):
    assert _full(prepped)[0] == 0
    valjean = next(p for p in fx.registry(prepped)["people"] if p["person_id"] == "valjean")
    assert valjean["mention_count"] is None
    assert valjean["mention_count_reason"]


def test_a_surface_two_people_claim_is_attributed_to_neither(prepped):
    """Attributing a shared printed form to whoever is more frequent is exactly
    the fabricated-person error this registry exists to avoid."""
    verdict = fx.verdict_doc(prepped)
    verdict["people"][1]["printed_surfaces"] = ["John"]   # Paul also claims "John"
    assert _through_claims(prepped, verdict)[0] == 0
    fx.write_adjudications(prepped)
    code, payload = fx.run(prepped, "--build")
    assert code == 0, payload
    reg = fx.registry(prepped)
    shared = {row["surface"]: row for row in reg["shared_printed_forms"]}
    assert "John" in shared
    assert sorted(shared["John"]["candidates"]) == ["john", "paul"]
    for person in reg["people"]:
        assert all(row["surface"] != "John" for row in person["printed_forms"])


def test_count_surfaces_boundary_matrix():
    """The counting rules, exercised directly on the pure function.

    Case by case: a longer surface consumes its span before a shorter one sees
    it; an UNOWNED longer form still consumes, or `Ben` would absorb
    `Ben-Gurion` (whose hyphen is not alphanumeric, so the boundary rule alone
    does not refuse it); a no-space script reports ambiguity rather than a
    false zero; and `none` counts substrings for such a target, at the
    documented cost.
    """
    corpus = "R. Nachman of Tulchin spoke. R. Nachman spoke again."
    got = pr.count_surfaces(["R. Nachman of Tulchin", "R. Nachman"], [], corpus, "word")
    assert got["R. Nachman of Tulchin"]["count"] == 1
    assert got["R. Nachman"]["count"] == 1        # not 2 -- the longer span was consumed

    got = pr.count_surfaces(["Ben"], ["Ben-Gurion"], "Ben-Gurion met Ben.", "word")
    assert got["Ben"]["count"] == 1

    # `Ann` occurs only inside `Anna` -- the SAME signature a no-space script
    # produces, which is why the ambiguity is reported rather than aborted on.
    got = pr.count_surfaces(["Ann"], [], "Anna spoke.", "word")
    assert got["Ann"]["status"] == "boundary_ambiguous"
    assert got["Ann"]["substring_count"] == 1

    got = pr.count_surfaces(["太郎"], [], "太郎は来た。", "word")
    assert got["太郎"]["status"] == "boundary_ambiguous"
    got = pr.count_surfaces(["太郎"], [], "太郎は来た。", "none")
    assert got["太郎"]["count"] == 1

    got = pr.count_surfaces(["Sylvestre"], [], "Nobody by that name.", "word")
    assert got["Sylvestre"]["status"] == "not_found_in_target_text"

    decomposed = "José spoke."
    got = pr.count_surfaces(["José"], [], pr.nfc(decomposed), "word")
    assert got["José"]["count"] == 1


def test_a_boundary_refused_span_is_consumed_exactly_as_the_renderer_consumes_it():
    """`render_obsidian.py`'s own worked case, counted rather than linked.

    Over the prose "JoAnn Marie", the targets "Ann Marie" and "Marie" both
    match; the longer one is boundary-refused (preceded by `o`), but the
    renderer's single non-overlapping `finditer` CONSUMES that span, so the
    shorter target gets no turn -- deliberately, because a rescan would attach
    a different entity's name inside a full name. Counting under a rescan
    instead would make the registry disagree with the vault about the same
    book, and the disagreement would read as a data problem rather than as two
    implementations of one decision.
    """
    corpus = "JoAnn Marie spoke."
    got = pr.count_surfaces(["Marie"], ["Ann Marie"], corpus, "word")
    assert got["Marie"]["count"] is None
    assert got["Marie"]["status"] == "boundary_ambiguous"

    # The same surface in the same corpus, once a properly bounded occurrence
    # exists: consumption never suppresses a real mention elsewhere.
    got = pr.count_surfaces(["Marie"], ["Ann Marie"], corpus + " Marie left.", "word")
    assert got["Marie"]["count"] == 1


# ---------------------------------------------------------------------------
# The adjudication binds to the VERDICT, not only to the prep
# ---------------------------------------------------------------------------

def test_a_verdict_edited_after_claims_is_refused(prepped):
    """An affirmation is valid only for the exact claim it was shown.

    `input_sha256` alone cannot see this: a verdict edited after `--claims`
    still cites the same prep, so the build would re-project a DIFFERENT claim
    set and apply Pass B's `claim_id`-keyed affirmations to it. Here the edit
    swaps which unit `mary` owns -- the affirmed sentence "these forms denote
    one person" would silently land on a different person.
    """
    assert _through_claims(prepped)[0] == 0
    fx.write_adjudications(prepped)
    tampered = fx.verdict_doc(prepped)
    tampered["people"][2]["display_name"] = "Marie, someone else entirely"
    tampered["people"][2]["identity_note"] = "a different woman"
    fx.write_verdict(prepped, tampered)
    code, payload = fx.run(prepped, "--build")
    assert code == 1
    assert payload["reason"] == "claims_stale"
    assert "registry_verdicts.json" in payload["error"]


def test_claims_and_registry_record_the_verdict_digest(prepped):
    code, payload = _through_claims(prepped)
    assert code == 0, payload
    claims = json.loads((prepped / "registry" / "registry_claims.json").read_text(encoding="utf-8"))
    verdict_bytes = (prepped / "registry" / "registry_verdicts.json").read_text(encoding="utf-8")
    assert claims["verdicts_sha256"] == pr.sha256_hex(json.loads(verdict_bytes))
    assert payload["verdicts_sha256"] == claims["verdicts_sha256"]
    fx.write_adjudications(prepped)
    assert fx.run(prepped, "--build")[0] == 0
    assert fx.registry(prepped)["provenance"]["verdicts_sha256"] == claims["verdicts_sha256"]


# ---------------------------------------------------------------------------
# A non-person classification is a judgement, so it is adjudicated too
# ---------------------------------------------------------------------------

def test_non_person_classification_is_projected_as_a_claim(prepped):
    assert _through_claims(prepped)[0] == 0
    claims = json.loads((prepped / "registry" / "registry_claims.json").read_text(encoding="utf-8"))
    rows = [c for c in claims["claims"] if c["kind"] == "non_person"]
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["unit"] == {"source_form": "Tulle", "sense_id": None}
    assert payload["claimed_kind"] == "place"
    assert payload["evidence"]["contexts"]        # the adjudicator can actually check it


def test_unaffirmed_non_person_becomes_a_refusal_not_a_silent_loss(prepped):
    """Removing a real person from the cast is exactly as silent as inventing
    one, so an unaffirmed classification does not stand."""
    assert _through_claims(prepped)[0] == 0
    fx.write_adjudications(prepped, refuse=lambda c: c["kind"] == "non_person")
    code, payload = fx.run(prepped, "--build")
    assert code == 0, payload
    reg = fx.registry(prepped)
    assert reg["non_person_forms"] == []
    refused = [r for r in reg["refusals"] if r["unit"]["source_form"] == "Tulle"]
    assert len(refused) == 1
    assert refused[0]["refused_by"] == "adjudication"
    assert refused[0]["reason"]
    assert any(r["kind"] == "non_person" for r in reg["refuted_claims"])
    assert reg["summary"]["non_person_forms"] == 0


# ---------------------------------------------------------------------------
# What Pass B is actually shown
# ---------------------------------------------------------------------------

def test_identity_note_is_adjudicated_not_waved_through(prepped):
    """`identity_note` reaches the reader verbatim in PEOPLE.md, so a relation
    asserted there is a claim like any other -- and the typed claims do not
    cover it."""
    assert _through_claims(prepped)[0] == 0
    claims = json.loads((prepped / "registry" / "registry_claims.json").read_text(encoding="utf-8"))
    person = next(c for c in claims["claims"] if c["kind"] == "person" and c["person_id"] == "john")
    assert person["payload"]["identity_note"] == "the son of Paul, of Tulle"
    assert "identity note" in person["question"]


def test_relation_claims_name_both_parties(prepped):
    """A bare `to_person_id` asks the adjudicator to confirm a claim "about
    these exact parties" while hiding one of them."""
    assert _through_claims(prepped)[0] == 0
    claims = json.loads((prepped / "registry" / "registry_claims.json").read_text(encoding="utf-8"))
    rel = next(c for c in claims["claims"]
               if c["kind"] == "relation" and c["person_id"] == "john")
    assert rel["payload"]["subject"]["display_name"] == "Jean, son of Paul"
    assert rel["payload"]["object"]["display_name"] == "Paul"
    assert rel["payload"]["object"]["units"][0]["canonical_target_form"] == "Paul"


def test_relation_to_someone_outside_the_cast_carries_no_object(prepped):
    verdict = fx.verdict_doc(prepped)
    verdict["people"][0]["relations"] = [
        {"type": "son_of", "to_unregistered": "Paul the elder",
         "evidence": {"quote": "Jean était le fils de Paul", "locator": dict(fx.LOC_1)}}
    ]
    assert _through_claims(prepped, verdict)[0] == 0
    claims = json.loads((prepped / "registry" / "registry_claims.json").read_text(encoding="utf-8"))
    rel = next(c for c in claims["claims"] if c["kind"] == "relation")
    assert "object" not in rel["payload"]
    assert rel["payload"]["claim"]["to_unregistered"] == "Paul the elder"


# ---------------------------------------------------------------------------
# Renderer parity, MEASURED against the renderer rather than asserted
# ---------------------------------------------------------------------------

def _renderer_links(entries: dict, corpus: str) -> set:
    """Which surfaces the SHIPPED renderer wraps, built the way `render()` does.

    `_Linker` grew `delinked_targets` and `diagnostic_pattern` in 1.32.0, and
    they change what it matches: without them a de-linked target's span is not
    consumed and a shorter name inside it still links. Constructing it with the
    defaults would quietly measure a linker production never builds — a parity
    test comparing against the wrong thing is worse than none, because it reads
    as evidence.
    """
    sys.path.insert(0, str(fx.ASSETS / "scripts"))
    try:
        import render_obsidian as ro
    finally:
        sys.path.remove(str(fx.ASSETS / "scripts"))
    pattern, target_to_entity = ro.build_entity_index(entries, {}, collision_delink=True)
    delinked = ro.delinked_owners_by_target(entries, None)
    linker = ro._Linker(pattern, target_to_entity, None,
                        diagnostic_pattern=ro.build_diagnostic_pattern(target_to_entity, delinked),
                        delinked_targets=set(delinked))
    return set(re.findall(r"\[\[[^\]|]*\|([^\]]+)\]\]", linker.link(corpus)))


@pytest.mark.parametrize(
    "corpus,targets",
    [
        # The overlap that separates leftmost-first from longest-first: the
        # longer target starts LATER, so a per-surface longest-first sweep and
        # the renderer's single alternation disagree about which one wins.
        ("JoAnn Marie spoke.", ["Ann Marie", "JoAnn"]),
        ("R. Nachman of Tulchin spoke.", ["Nachman of Tulchin", "R. Nachman"]),
        # Same start offset: longest-first is the tiebreak, and both agree.
        ("R. Nachman of Tulchin spoke.", ["R. Nachman of Tulchin", "R. Nachman"]),
        # Longer target starts EARLIER: also agreement, and pinned because the
        # entry's claim is about the LATER case specifically -- a claim stated
        # as "whenever they start at different offsets" would be false here.
        ("R. Nachman of Tulchin spoke.", ["R. Nachman of Tulchin", "Tulchin"]),
        # A refused span is consumed by both, so `Marie` gets no turn.
        ("JoAnn Marie spoke.", ["Ann Marie", "Marie"]),
        # An unowned longer form still consumes; a hyphen is not alphanumeric.
        ("Ben-Gurion met Ben.", ["Ben-Gurion", "Ben"]),
        # Refused outright by the boundary rule, by both.
        ("Anna spoke.", ["Ann"]),
        ("Tepliker prose.", ["Teplik"]),
    ],
)
def test_counting_selects_the_same_spans_the_shipped_renderer_links(corpus, targets):
    """Parity with `render_obsidian.py` measured by RUNNING it, not asserted.

    The registry and the vault must agree about the same book: a name counted
    here under a rule the renderer does not link under would read as a data
    problem rather than as two implementations of one decision. So this drives
    the shipped `build_entity_index` + `_Linker` over each corpus and compares
    which surfaces it wraps against which surfaces `count_surfaces` counts.
    Every target occurs at most once per corpus, so the linker's one-wikilink-
    per-block rule cannot mask a disagreement.
    """
    entries = {f"s{i}": {"canonical_target_form": t, "basis": "transliterated", "is_proper_name": True}
               for i, t in enumerate(targets)}
    linked = _renderer_links(entries, corpus)

    got = pr.count_surfaces(targets, [], pr.nfc(corpus), "word")
    counted = {surface for surface, row in got.items() if row["count"]}
    assert counted == linked, f"registry counted {sorted(counted)}, renderer linked {sorted(linked)}"


def test_a_contested_status_is_adjudicated_too_reason_and_all(prepped):
    """The conflation the issue names outright: `contested` "because one mention
    only" is a statement about SCARCITY, and `identity_status` is about whether
    the person can be told apart from a namesake. `contested` is the safe
    status, but its reason reaches the reader, so it is adjudicated like any
    other assertion -- and an unaffirmed one is replaced, not published.
    """
    assert _through_claims(prepped)[0] == 0
    claims = json.loads((prepped / "registry" / "registry_claims.json").read_text(encoding="utf-8"))
    status = [c for c in claims["claims"] if c["kind"] == "identity_status"]
    assert {c["person_id"] for c in status} == {"john", "paul", "mary", "valjean"}
    paul = next(c for c in status if c["person_id"] == "paul")
    assert paul["payload"]["claimed_status"] == "contested"
    assert paul["payload"]["claimed_reason"] == "one mention only"
    assert "scarcity" in paul["question"]

    fx.write_adjudications(
        prepped,
        refuse=lambda c: c["kind"] == "identity_status" and c["person_id"] == "paul",
    )
    code, payload = fx.run(prepped, "--build")
    assert code == 0, payload
    reg = fx.registry(prepped)
    paul_out = next(p for p in reg["people"] if p["person_id"] == "paul")
    assert paul_out["identity_status"] == "contested"
    assert "did not affirm the stated identity status" in paul_out["identity_status_reason"]
    assert "one mention only" not in paul_out["identity_status_reason"]
    assert any(r["kind"] == "identity_status" and r["person_id"] == "paul"
               and r["reason"] == "not established by the contexts shown"
               for r in reg["refuted_claims"])


def test_printed_surface_claims_carry_the_delivered_text(prepped):
    """A model asked "does the book print this string" while shown only the
    SOURCE contexts is guessing, and so is the reader checking it."""
    assert _through_claims(prepped)[0] == 0
    claims = json.loads((prepped / "registry" / "registry_claims.json").read_text(encoding="utf-8"))
    john = next(c for c in claims["claims"]
                if c["kind"] == "printed_surface" and c["payload"]["surface"] == "John")
    # 7 raw appearances -- three in the nodes (one of them `Johnson`), one in
    # the footnote, and one in each half of the verse. Deliberately the RAW
    # count, not the bounded one: `Johnson` is exactly the kind of appearance
    # the adjudicator has to see in order to rule it out.
    assert john["payload"]["target_occurrence_total"] == 7
    windows = john["payload"]["target_occurrences"]
    assert windows and len(windows) == len(set(windows))
    assert all("John" in w for w in windows)


def test_a_surface_the_book_never_prints_reaches_the_adjudicator_as_an_empty_list(prepped):
    verdict = fx.verdict_doc(prepped)
    verdict["people"][1]["printed_surfaces"] = ["Paul", "Sylvestre"]
    assert _through_claims(prepped, verdict)[0] == 0
    claims = json.loads((prepped / "registry" / "registry_claims.json").read_text(encoding="utf-8"))
    row = next(c for c in claims["claims"]
               if c["kind"] == "printed_surface" and c["payload"]["surface"] == "Sylvestre")
    assert row["payload"]["target_occurrences"] == []
    assert row["payload"]["target_occurrence_total"] == 0


def test_contexts_pair_the_source_occurrence_with_the_delivered_text(prepped):
    """Each context carries what the book PRINTS in that same container, so a
    rendering unlike the canonical form is visible rather than imagined."""
    doc = json.loads((prepped / "registry" / "registry_input.json").read_text(encoding="utf-8"))
    jean = next(u for u in doc["units"] if u["unit"]["source_form"] == "Jean")
    by_origin = {c["locator"]["origin"]: c for c in jean["contexts"]}
    assert set(by_origin) == {"block", "embedded_verse", "footnote"}
    assert by_origin["footnote"]["target_text"] == fx.FOOTNOTE_TEXT
    assert fx.VERSE_RENDERED in by_origin["embedded_verse"]["target_text"]
    assert fx.VERSE_GLOSS in by_origin["embedded_verse"]["target_text"]
    assert all(c["target_window_centred_on_canonical_form"] for c in jean["contexts"])


def test_a_duplicated_typed_claim_is_refused_by_the_verdict_schema(prepped):
    """Two identical relation rows project to ONE claim_id, so a single Pass B
    row would satisfy both and B3 would emit the edge twice. Refused upstream,
    where the duplicate is visible, rather than deduplicated silently."""
    verdict = fx.verdict_doc(prepped)
    verdict["people"][0]["relations"] *= 2
    fx.write_verdict(prepped, verdict)
    code, payload = fx.run(prepped, "--claims")
    assert code == 1
    assert payload["reason"] == "verdicts_schema_invalid"


# ---------------------------------------------------------------------------
# The class, not the instance: nothing a model wrote reaches the reader unjudged
# ---------------------------------------------------------------------------

def test_every_model_written_string_reaching_the_artifact_is_inside_a_claim(prepped):
    """A structural guard over the CLASS the round-1 and round-2 reviews each
    found one instance of (`identity_note`, then the `contested` reason).

    Enumerating fields one at a time closes instances; this closes the class. It
    stamps a unique marker into every free-text field of the verdict, runs the
    whole pass, and asserts that every marker that survives into
    `person_registry.json` also appears inside `registry_claims.json` — i.e.
    some adjudicator was actually shown it. A future field added to the verdict
    and copied to the artifact without a claim fails here, whatever it is called.

    ONE carve-out, deliberate and named: `refusals[].reason` — Pass A's own
    account of why it declined to place a unit. A refusal is the safe direction
    and adjudicating it would be asking a second model to talk the first out of
    caution; the reason is published as Pass A's, in a sink documented as such.

    `test_the_marker_sweep_covers_every_string_field_the_verdict_schema_allows`
    is the other half: this test proves the marked fields are adjudicated, that
    one proves the marked set is the whole schema. Neither is worth much alone.
    """
    verdict = fx.verdict_doc(prepped)
    marked = {}

    def mark(key, value):
        token = f"MARK{len(marked):03d}"
        marked[token] = key
        return f"{token} {value}"

    for person in verdict["people"]:
        pid = person["person_id"]
        person["display_name"] = mark(f"{pid}.display_name", person["display_name"])
        person["identity_note"] = mark(f"{pid}.identity_note", person["identity_note"])
        person["identity_status_reason"] = mark(f"{pid}.identity_status_reason",
                                                person["identity_status_reason"])
        person["printed_surfaces"] = [mark(f"{pid}.printed_surface", s)
                                      for s in person["printed_surfaces"]]
        for row in person["relations"]:
            if "to_unregistered" in row:
                row["to_unregistered"] = mark(f"{pid}.to_unregistered", row["to_unregistered"])
        for row in person["places"]:
            row["name"] = mark(f"{pid}.place_name", row["name"])
        for row in person["dates"]:
            row["value"] = mark(f"{pid}.date_value", row["value"])
    for row in verdict["non_person_forms"]:
        row["reason"] = mark("non_person.reason", row["reason"])

    assert _through_claims(prepped, verdict)[0] == 0
    fx.write_adjudications(prepped)
    code, payload = fx.run(prepped, "--build")
    assert code == 0, payload

    registry_text = (prepped / "registry" / "person_registry.json").read_text(encoding="utf-8")
    claims_text = (prepped / "registry" / "registry_claims.json").read_text(encoding="utf-8")

    in_artifact = {t for t in marked if t in registry_text}
    assert len(in_artifact) >= 12, sorted(in_artifact)      # never vacuously empty
    unjudged = sorted(marked[t] for t in in_artifact if t not in claims_text)
    assert unjudged == [], f"reaches the reader without ever being adjudicated: {unjudged}"


def test_people_md_names_the_person_a_relation_points_at(prepped):
    """`person_id` is an identifier; PEOPLE.md is what a genealogy reader reads.
    An edge rendered as `son_of paul` makes them look it up; the JSON keeps the
    id, which is what an importer needs."""
    assert _full(prepped)[0] == 0
    text = (prepped / "registry" / "PEOPLE.md").read_text(encoding="utf-8")
    assert "**son_of** Paul —" in text
    assert "**wife_of** Jean, son of Paul —" in text
    assert "son_of paul" not in text


def test_people_md_marks_a_relation_target_outside_the_cast(prepped):
    verdict = fx.verdict_doc(prepped)
    verdict["people"][0]["relations"] = [
        {"type": "son_of", "to_unregistered": "Paul the elder",
         "evidence": {"quote": "Jean était le fils de Paul", "locator": dict(fx.LOC_1)}}
    ]
    assert _through_claims(prepped, verdict)[0] == 0
    fx.write_adjudications(prepped)
    assert fx.run(prepped, "--build")[0] == 0
    text = (prepped / "registry" / "PEOPLE.md").read_text(encoding="utf-8")
    assert "**son_of** Paul the elder _(not in this book's cast)_" in text


# ---------------------------------------------------------------------------
# Round-3 fixes
# ---------------------------------------------------------------------------

def test_surface_evidence_truncation_is_disclosed_not_implied():
    """The cap is the same accepted tradeoff the source contexts make -- a name
    printed three hundred times cannot put three hundred windows in one claim.
    What is NOT acceptable is a capped list presented as a complete one, so the
    true total and the truncation flag travel with it and the question says so.
    """
    corpus = " ".join(f"Sentence {i} about Rivka and others." for i in range(9))
    windows, total, truncated = pr.surface_windows(corpus, "Rivka", 4, 30)
    assert total == 9
    assert truncated is True
    assert len(windows) <= 4
    windows, total, truncated = pr.surface_windows(corpus, "Rivka", 20, 30)
    assert (total, truncated) == (9, False)


def test_a_relation_to_a_refused_person_is_refuted_not_left_dangling(prepped):
    """`A -> B` can be affirmed while B's own identity claim is not. Judging
    referential integrity against Pass A's cast -- before adjudication -- leaves
    an edge pointing at a person the registry does not contain."""
    assert _through_claims(prepped)[0] == 0
    claims = json.loads((prepped / "registry" / "registry_claims.json").read_text(encoding="utf-8"))
    rows = []
    for claim in claims["claims"]:
        # Refuse PAUL's identity, but affirm john's `son_of paul` relation.
        deny = claim["kind"] == "person" and claim["person_id"] == "paul"
        rows.append({"claim_id": claim["claim_id"], "affirmed": not deny,
                     "reason": "two men share this form" if deny else "stated"})
    (prepped / "registry" / "registry_adjudications.json").write_text(
        json.dumps({"schema_version": 1, "input_sha256": claims["input_sha256"],
                    "claims_sha256": claims["claims_sha256"], "adjudications": rows},
                   ensure_ascii=False), encoding="utf-8")
    code, payload = fx.run(prepped, "--build")
    assert code == 0, payload
    reg = fx.registry(prepped)
    assert "paul" not in {p["person_id"] for p in reg["people"]}
    john = next(p for p in reg["people"] if p["person_id"] == "john")
    assert all(r.get("to_person_id") != "paul" for r in john["relations"])
    assert any(r["reason"] == "relation_target_identity_not_affirmed"
               for r in reg["refuted_claims"])
    assert "son_of paul" not in (prepped / "registry" / "PEOPLE.md").read_text(encoding="utf-8")


def test_a_footnote_the_renderer_never_emits_is_not_counted(prepped):
    """`assemble.py` puts footnotes discovered THROUGH a definition-embedded
    verse into the NodeStream while keeping them out of every node's `fnrefs`,
    because that verse is stripped rather than rendered; `render_obsidian.py`
    emits only the footnotes it reaches through `fnrefs`. Counting the raw list
    would report a printed name on a page no reader is ever shown."""
    ns_path = prepped / "out" / ".assembled" / "nodestream.json"
    ns = json.loads(ns_path.read_text(encoding="utf-8"))
    ns["footnotes"].append({"n": 99, "text": "A nested note naming John, delivered nowhere."})
    assert all(99 not in (node.get("fnrefs") or []) for node in ns["nodes"])
    ns_path.write_text(json.dumps(ns, ensure_ascii=False, indent=1), encoding="utf-8")

    code, payload = fx.run(prepped, "--prep")
    assert code == 0, payload
    assert _full(prepped)[0] == 0
    john = next(p for p in fx.registry(prepped)["people"] if p["person_id"] == "john")
    printed = {row["surface"]: row for row in john["printed_forms"]}
    assert printed["John"]["count"] == 6      # unchanged: the nested note is not delivered


def test_a_declared_non_name_target_still_consumes_its_span(prepped):
    """The renderer alternates over EVERY canon entry and never branches on
    `is_proper_name`, so a target the project declared realia still takes its
    span in the delivered text. Dropping it from the consumption inventory lets
    a short person-surface absorb an occurrence that is not theirs."""
    canon_path = prepped / "canon.json"
    canon = json.loads(canon_path.read_text(encoding="utf-8"))
    canon["entries"]["Le Livre"]["canonical_target_form"] = "John Book"
    canon_path.write_text(json.dumps(canon, ensure_ascii=False, indent=1), encoding="utf-8")

    ns_path = prepped / "out" / ".assembled" / "nodestream.json"
    ns = json.loads(ns_path.read_text(encoding="utf-8"))
    ns["nodes"][0]["text"] = ns["nodes"][0]["text"] + " He read the John Book aloud."
    ns_path.write_text(json.dumps(ns, ensure_ascii=False, indent=1), encoding="utf-8")

    assert fx.run(prepped, "--prep")[0] == 0
    doc = json.loads((prepped / "registry" / "registry_input.json").read_text(encoding="utf-8"))
    excluded = {row["source_form"]: row for row in doc["excluded_by_canon_declaration"]}
    assert excluded["Le Livre"]["canonical_target_form"] == "John Book"

    assert _full(prepped)[0] == 0
    john = next(p for p in fx.registry(prepped)["people"] if p["person_id"] == "john")
    printed = {row["surface"]: row for row in john["printed_forms"]}
    # 6 as before -- the added `John Book` is consumed by its own (unowned)
    # target and is not a seventh John.
    assert printed["John"]["count"] == 6


# Every string-valued field `registry-verdicts.schema.json` allows, and what
# happens to it. A field added to the schema later appears here as an unknown
# path and fails, which is the point: the decision has to be made, not skipped.
# `marked` = stamped by the marker sweep above, so proven to reach an
# adjudicator. Everything else needs a stated reason for why it does not.
_VERDICT_STRING_FIELDS = {
    "input_sha256": "a digest, recomputed by the build",
    "people[].person_id": "an opaque key, checked for uniqueness and referential integrity",
    "people[].display_name": "marked",
    "people[].identity_note": "marked",
    "people[].identity_status": "a two-value enum, adjudicated with its reason",
    "people[].identity_status_reason": "marked",
    "people[].printed_surfaces[]": "marked",
    "people[].units[].source_form": "joined to the prep universe by P3/P4; not free text",
    "people[].units[].sense_id": "joined to the prep universe by P3/P4; not free text",
    "people[].relations[].type": "a closed enum",
    "people[].relations[].to_person_id": "an id, resolved to an identity card in the claim and "
                                         "re-checked against the surviving cast after B3",
    "people[].relations[].to_unregistered": "marked",
    "people[].relations[].evidence.quote": "verified verbatim against its container by P5 and "
                                           "carried into the typed claim",
    "people[].relations[].evidence.locator.origin": "a closed enum",
    "people[].relations[].evidence.locator.block": "a container id, resolved by P5",
    "people[].relations[].evidence.locator.vid": "a container id, resolved by P5",
    "people[].places[].role": "a closed enum",
    "people[].places[].name": "marked",
    "people[].places[].evidence.quote": "verified verbatim against its container by P5",
    "people[].places[].evidence.locator.origin": "a closed enum",
    "people[].places[].evidence.locator.block": "a container id, resolved by P5",
    "people[].places[].evidence.locator.vid": "a container id, resolved by P5",
    "people[].dates[].kind": "a closed enum",
    "people[].dates[].value": "marked",
    "people[].dates[].evidence.quote": "verified verbatim against its container by P5",
    "people[].dates[].evidence.locator.origin": "a closed enum",
    "people[].dates[].evidence.locator.block": "a container id, resolved by P5",
    "people[].dates[].evidence.locator.vid": "a container id, resolved by P5",
    "non_person_forms[].kind": "a closed enum, adjudicated with its reason",
    "non_person_forms[].reason": "marked",
    "non_person_forms[].unit.source_form": "joined to the prep universe by P3/P4",
    "non_person_forms[].unit.sense_id": "joined to the prep universe by P3/P4",
    "refusals[].reason": "THE carve-out: Pass A's own account of why it declined, published as "
                         "such in the refusal sink",
    "refusals[].unit.source_form": "joined to the prep universe by P3/P4",
    "refusals[].unit.sense_id": "joined to the prep universe by P3/P4",
}


def _schema_string_paths(schema: dict) -> set:
    defs = schema.get("$defs") or {}
    found = set()

    def resolve(node):
        for _ in range(10):
            if not (isinstance(node, dict) and "$ref" in node):
                break
            node = defs[node["$ref"].rsplit("/", 1)[-1]]
        return node

    def walk(node, path, depth=0):
        node = resolve(node)
        if not isinstance(node, dict) or depth > 12:
            return
        for branch in ("allOf", "anyOf", "oneOf"):
            for sub in node.get(branch) or []:
                walk(sub, path, depth + 1)
        if node.get("properties"):
            for key, value in node["properties"].items():
                walk(value, f"{path}.{key}" if path else key, depth + 1)
            return
        if node.get("items") is not None:
            walk(node["items"], f"{path}[]", depth + 1)
            return
        declared = node.get("type")
        if "string" in (declared if isinstance(declared, list) else [declared]):
            found.add(path)

    walk(schema, "")
    return found


def test_the_marker_sweep_covers_every_string_field_the_verdict_schema_allows():
    """The half that makes the marker sweep a CLASS guard rather than a list.

    The sweep above can only prove things about fields it stamps. This one walks
    `registry-verdicts.schema.json` itself and requires every string-valued path
    to be accounted for — either stamped, or carrying a written reason why not.
    A field added to the schema and copied into the artifact without a claim
    shows up here as an unknown path, before anyone can rely on it.

    Scope, stated rather than implied: the walk follows `properties`, `items`,
    `$defs` refs and `allOf`/`anyOf`/`oneOf`. It does NOT model
    `additionalProperties` schemas, `patternProperties`, `prefixItems` or
    `contains` — none of which this schema uses, each of which would need its
    own traversal. It is a guard over the shape this schema actually has, not a
    general JSON-Schema walker.
    """
    schema = json.loads((fx.ASSETS / "schemas" / "registry" / "registry-verdicts.schema.json")
                        .read_text(encoding="utf-8"))
    paths = _schema_string_paths(schema)
    assert len(paths) > 25, sorted(paths)          # never vacuously empty
    unaccounted = sorted(paths - set(_VERDICT_STRING_FIELDS))
    assert unaccounted == [], (
        "new string field(s) in registry-verdicts.schema.json with no decision recorded: "
        f"{unaccounted}"
    )
    stale = sorted(set(_VERDICT_STRING_FIELDS) - paths)
    assert stale == [], f"recorded decisions for fields the schema no longer has: {stale}"


# ---------------------------------------------------------------------------
# The verse seam
# ---------------------------------------------------------------------------

def test_no_surface_is_ever_matched_across_a_container_seam():
    """`John⟦VERSE…⟧Smith` must not produce a countable `John Smith`.

    The renderer resolves that placeholder to the verse wrapped in its own
    markup, so no printed name spans the join. Replacing the placeholder with a
    SPACE fabricates one; splicing the verse's own text in bare fabricates a
    different one; leaving the raw token puts a string in the corpus that is
    printed nowhere. A hard seam is the only option that cannot invent a name,
    and the verse's delivered halves are appended in full anyway.
    """
    nodestream = {
        "nodes": [{
            "id": "PARA:seg01:0001", "seg": "seg01", "text": "John⟦VERSE_V9_abc⟧Smith spoke.",
            "fnrefs": [], "verses": [{"vid": "V9", "placeholder": "⟦VERSE_V9_abc⟧",
                                      "content": {"rendered": "an interlude", "literal_gloss": ""}}],
        }],
        "footnotes": [],
    }
    corpus = pr.assembled_target_text(nodestream)
    assert "⟦" not in corpus and "VERSE_V9" not in corpus
    assert "an interlude" in corpus
    got = pr.count_surfaces(["John Smith", "John", "Smith"], [], corpus, "word")
    assert got["John Smith"]["status"] == "not_found_in_target_text"
    assert got["John"]["count"] == 1
    assert got["Smith"]["count"] == 1


def test_a_standalone_verse_shows_its_delivered_text_not_its_placeholder():
    """A `mount: "block"` verse's node text is NOTHING but the placeholder, and
    `occurrence_targets` reports its occurrences as block-origin. An index over
    raw node text would hand both models `⟦VERSE_…⟧` where the delivered
    rendering belongs — the silent under-coverage the pairing exists to stop."""
    nodestream = {
        "nodes": [{
            "id": "PARA:seg01:0007", "seg": "seg01", "text": "⟦VERSE_V2_beef⟧",
            "fnrefs": [], "verses": [{"vid": "V2", "placeholder": "⟦VERSE_V2_beef⟧",
                                      "content": {"rendered": "John in verse",
                                                  "literal_gloss": "John, literally"}}],
        }],
        "footnotes": [],
    }
    index = pr.build_target_index(nodestream)
    delivered = pr.resolve_target_container(index, {"origin": "block", "block": "PARA:seg01:0007"})
    assert "⟦" not in delivered
    assert "John in verse" in delivered and "John, literally" in delivered


def test_a_de_linked_collision_target_is_consumed_by_both():
    """1.32.0 closed a divergence this pass used to have to document.

    When two canon entries own one target the renderer de-links it — and since
    #588 its single scan CONSUMES that span, so a shorter name nested inside it
    gets no turn. That is what this counter already did, for its own reason (the
    book prints `John Book`; counting a `John` inside it for a person named John
    is a wrong number, and nothing downstream catches a wrong number). Measured
    against the shipped renderer rather than assumed, because the two arrived at
    it independently and could drift apart again.
    """
    entries = {
        "Le Livre": {"canonical_target_form": "John Book", "basis": "transliterated"},
        "Das Buch": {"canonical_target_form": "John Book", "basis": "transliterated"},
        "Jean": {"canonical_target_form": "John", "basis": "transliterated"},
    }
    corpus = "He read the John Book aloud."
    assert _renderer_links(entries, corpus) == set()
    got = pr.count_surfaces(["John"], ["John Book"], pr.nfc(corpus), "word")
    assert got["John"]["status"] == "boundary_ambiguous"

    # And neither suppresses a genuine standalone mention elsewhere.
    corpus = "He read the John Book aloud. John spoke later."
    assert _renderer_links(entries, corpus) == {"John"}
    assert pr.count_surfaces(["John"], ["John Book"], pr.nfc(corpus), "word")["John"]["count"] == 1


def test_a_sense_translated_only_target_is_the_one_divergence_that_remains():
    """The inventory is still WIDER than the renderer's links in exactly one
    shape, and it is deliberate.

    A target owned only by `sense_translated` entries is dropped from the
    renderer's index entirely — not de-linked, absent — so it consumes nothing
    and the renderer links a shorter name inside it. Correct for a link, where
    the alternative is no link at all. Counting is a different question, and the
    answer that serves a genealogy registry is the same as for a collision: the
    book prints `John Book`, so `John` inside it is not a mention of a person
    named John. Pinned so the difference stays a decision rather than becoming
    an accident.
    """
    entries = {
        "Le Livre": {"canonical_target_form": "John Book", "basis": "sense_translated"},
        "Jean": {"canonical_target_form": "John", "basis": "transliterated"},
    }
    corpus = "He read the John Book aloud."
    assert _renderer_links(entries, corpus) == {"John"}          # the renderer links it
    got = pr.count_surfaces(["John"], ["John Book"], pr.nfc(corpus), "word")
    assert got["John"]["status"] == "boundary_ambiguous"          # the counter does not count it


# ---------------------------------------------------------------------------
# PEOPLE.md is a deliverable, and a model writes the strings it is built from
# ---------------------------------------------------------------------------

def test_a_line_break_in_an_identity_field_is_refused(prepped):
    """`PEOPLE.md` is assembled by interpolating model-written strings into
    headings and bullets, so a `display_name` carrying `\\n\\n## Refused` writes a
    section no adjudication produced, and an `identity_note` carrying
    `\\n- **son_of** X` writes a kinship edge indistinguishable from an affirmed
    one. No adversary is required — a model emitting a two-line note does it by
    accident, and the forged bullet is exactly the fabricated relation this
    whole design exists to prevent.
    """
    verdict = fx.verdict_doc(prepped)
    verdict["people"][0]["display_name"] = "Jean\n\n## Refused\n\n- `Everyone` — fabricated"
    fx.write_verdict(prepped, verdict)
    code, payload = fx.run(prepped, "--claims")
    assert code == 1
    assert payload["reason"] == "line_break_in_field"
    assert "display_name" in payload["error"]


@pytest.mark.parametrize("field,value", [
    ("identity_note", "the son of Paul\n- **son_of** Nobody — “forged”"),
    ("identity_status_reason", "settled\n## Printed forms shared by several people"),
])
def test_every_identity_field_is_covered_not_just_the_name(prepped, field, value):
    verdict = fx.verdict_doc(prepped)
    verdict["people"][0][field] = value
    fx.write_verdict(prepped, verdict)
    code, payload = fx.run(prepped, "--claims")
    assert code == 1 and payload["reason"] == "line_break_in_field"
    assert field in payload["error"]


def test_a_multi_line_quote_survives_verbatim_in_json_and_collapses_in_markdown(prepped):
    """A verse spans lines, and its quote has to stay verbatim to remain
    verifiable against its container — so quotes are NOT refused. The Markdown
    collapses them instead: the JSON keeps the break, and a bullet cannot be
    split into a second, forged bullet by the contents of a quote."""
    ns_path = prepped / "manifest.json"
    manifest = json.loads(ns_path.read_text(encoding="utf-8"))
    manifest["verse"]["store"][0]["plain_text"] = "Jean, frère de Marie,\nchantait."
    ns_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    assert fx.run(prepped, "--prep")[0] == 0

    # Built AFTER the re-prep: a verdict cites the prep digest, and P2 refuses
    # one raised against different bytes.
    verdict = fx.verdict_doc(prepped)
    verdict["people"][0]["dates"] = []
    verdict["people"][0]["places"] = []
    verdict["people"][0]["relations"] = [
        {"type": "brother_of", "to_person_id": "mary",
         "evidence": {"quote": "Jean, frère de Marie,\nchantait", "locator": dict(fx.LOC_VERSE)}}
    ]
    assert _through_claims(prepped, verdict)[0] == 0
    fx.write_adjudications(prepped)
    assert fx.run(prepped, "--build")[0] == 0

    jean = next(p for p in fx.registry(prepped)["people"] if p["person_id"] == "john")
    assert jean["relations"][0]["quote"] == "Jean, frère de Marie,\nchantait"   # verbatim
    text = (prepped / "registry" / "PEOPLE.md").read_text(encoding="utf-8")
    assert "“Jean, frère de Marie, chantait”" in text                            # one line
    assert "chantait”" in text and "\nchantait" not in text
