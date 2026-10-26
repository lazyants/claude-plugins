"""Offline continuation fixtures: source gaps, shared-budget checkpoints and recovery."""

import copy
from pathlib import Path

import pytest

import test_packets as fx

canon = fx.canon_mod
packets = fx.packets
COMPLETE = {"status": "complete", "remaining": []}
LIMITED = {"status": "resource_limited", "remaining": ["Search the reference help centre"]}


def term(progress=COMPLETE, **kwargs):
    return {**fx.research_term(**kwargs), "progress": copy.deepcopy(progress)}


def workspace(root, ids=("t-gap", "t-limited")):
    fx.setup_research_workspace(root, [fx.make_canon_entry(i, i) for i in ids])


def selected(root, kind="research"):
    result = packets.do_build(root, kind, "de", fx.TEMPLATES_DIR)
    return [item for batch in result["batches"] for item in fx.packet_of(batch)["items"]]


@pytest.mark.parametrize("legacy", [False, True])
def test_completed_source_gap_is_distinct_from_unperformed_search(work_root, legacy):
    workspace(work_root)
    gap = term(findings=[], confidence="low", note="Bounded search found no external usage.")
    if legacy:
        gap.pop("progress")
    limited = term(LIMITED, findings=[], confidence="low", note="Tool allowance exhausted.")
    batch = fx.build_research_batch(work_root, ["t-gap", "t-limited"])
    result = fx.research_accept(work_root, batch, {"terms": {"t-gap": gap, "t-limited": limited}})

    assert result["stored"] == ["t-gap", "t-limited"]
    assert result["resource_limited"] == ["t-limited"]
    assert [item["id"] for item in selected(work_root)] == ["t-limited"]
    assert [item["id"] for item in selected(work_root, "research_verify")] == ["t-gap"]
    report = fx.report_mod.build_report(work_root, "de")
    assert "researched: 1, verified: 0" in report
    assert "research progress: complete" in report or legacy
    pending = report.split("## Canon work awaiting another turn", 1)[1]
    assert "`t-limited` / research: resource_limited" in pending
    assert "`t-gap`" not in pending
    assert LIMITED["remaining"][0] in pending
    assert str(Path(batch["dir"]) / "packet.json") in pending


def test_research_continuation_keeps_evidence_siblings_raw_answers_and_packets(work_root):
    workspace(work_root)
    original = fx.build_research_batch(work_root, ["t-gap", "t-limited"])
    files = {name: (Path(original["dir"]) / name).read_bytes() for name in ("packet.json", "prompt.md")}
    partial = term(LIMITED, confidence="low", note="Only the first source could be checked.")
    fx.research_accept(work_root, original, {"terms": {"t-gap": term(), "t-limited": partial}})
    sibling = copy.deepcopy(fx.research_records(work_root)["t-gap"])
    other_locale = {"schema": 1, "locale": "ru", "entries": {"untouched": {"raw": "Данные"}}}
    canon.save_research(work_root, "ru", other_locale)
    before_ru = canon.research_path(work_root, "ru").read_bytes()

    continuation = packets.do_build(work_root, "research", "de", fx.TEMPLATES_DIR)["batches"][0]
    item = fx.packet_of(continuation)["items"][0]
    assert item["resume"]["previous"] == partial
    assert item["resume"]["packet"] == str(Path(original["dir"]) / "packet.json")
    new_finding = {**partial["findings"][0], "url": "https://example.org/b", "quote": "A second exact quote"}
    answer = term(findings=[new_finding], note="The remaining query supports the same sense.")
    result = fx.research_accept(work_root, continuation, {"terms": {"t-limited": answer}})
    record = fx.research_records(work_root)["t-limited"]

    assert result["stored"] == ["t-limited"] and result["resource_limited"] == []
    assert record["research"]["findings"] == partial["findings"] + [new_finding]
    assert [attempt["answer"] for attempt in record["attempts"]] == [partial, answer]
    assert record["verification"] is None
    assert fx.research_records(work_root)["t-gap"] == sibling
    assert canon.research_path(work_root, "ru").read_bytes() == before_ru
    assert all((Path(original["dir"]) / name).read_bytes() == raw for name, raw in files.items())
    assert selected(work_root) == []
    before = canon.research_path(work_root, "de").read_bytes()
    replay = fx.research_accept(work_root, original, {"terms": {"t-gap": term(), "t-limited": partial}})
    assert replay["stored"] == [] and replay["stale"] == ["t-gap", "t-limited"]
    assert canon.research_path(work_root, "de").read_bytes() == before


def test_repeated_exhaustion_keeps_remaining_work_and_original_packet(work_root):
    workspace(work_root, ("t-a",))
    original = fx.build_research_batch(work_root, ["t-a"])
    original_packet = (Path(original["dir"]) / "packet.json").read_bytes()
    for remaining in (["Search A", "Search B"], ["Search B"]):
        batch = fx.build_research_batch(work_root, ["t-a"])
        result = fx.research_accept(work_root, batch, {"terms": {"t-a": term(
            {"status": "resource_limited", "remaining": remaining}, confidence="low")}})
        assert result["resource_limited"] == ["t-a"]
    item = selected(work_root)[0]
    assert item["resume"]["previous"]["progress"]["remaining"] == ["Search B"]
    assert len(fx.research_records(work_root)["t-a"]["attempts"]) == 2
    assert (Path(original["dir"]) / "packet.json").read_bytes() == original_packet


def test_limited_research_is_still_pending_after_approval(work_root):
    workspace(work_root, ("t-a",))
    fx.research_accept(work_root, fx.build_research_batch(work_root, ["t-a"]), {"terms": {"t-a": term(LIMITED)}})
    data = canon.load(work_root)
    data["entries"][0]["translations"]["de"] = {"value": "Konto", "status": "approved"}
    canon.save(work_root, data)
    assert [i["id"] for i in selected(work_root)] == ["t-a"]
    with pytest.raises(SystemExit):
        fx.build_verify_batch(work_root, ["t-a"])
    assert canon.research_summary(fx.research_records(work_root)["t-a"])["research_progress"] == LIMITED


def partial_verification(root):
    workspace(root, ("t-a", "t-sibling"))
    findings = fx.research_term()["findings"]
    findings += [{**findings[0], "url": "https://example.org/b", "quote": "A second quote"}]
    fx.research_accept(root, fx.build_research_batch(root, ["t-a", "t-sibling"]), {
        "terms": {i: term(findings=findings) for i in ("t-a", "t-sibling")}})
    batch = fx.build_verify_batch(root)
    items = {i["id"]: i for i in fx.packet_of(batch)["items"]}
    partial = fx.verdict_for(items["t-a"], verdict="weak", findings=[
        {"index": 0, "confirmed": False, "detail": "Actually attempted: page blocks the fetch."}],
        progress={"status": "resource_limited", "remaining": ["Fetch source index 1", "Check counter-evidence"]})
    sibling = fx.verdict_for(items["t-sibling"], progress=COMPLETE)
    result = fx.research_accept(root, batch, {"verdicts": {"t-a": partial, "t-sibling": sibling}})
    assert result["verified"] == ["t-sibling"] and result["resource_limited"] == ["t-a"]
    return batch, partial


def test_verification_resumes_only_unchecked_indexes_and_keeps_completed_siblings(work_root):
    original, partial = partial_verification(work_root)
    sibling = copy.deepcopy(fx.research_records(work_root)["t-sibling"])
    record = fx.research_records(work_root)["t-a"]
    assert canon.research_summary(record)["verdict"] is None
    assert "verification resource limited" in canon.research_flags(record)
    assert "researched: 2, verified: 1" in fx.report_mod.build_report(work_root, "de")

    batch = fx.build_verify_batch(work_root)
    assert batch["dir"] != original["dir"]
    item, = fx.packet_of(batch)["items"]
    assert item["resume"]["previous"]["findings"] == partial["findings"]
    answer = fx.verdict_for(item, verdict="weak", progress=COMPLETE, findings=[
        {"index": 1, "confirmed": True, "detail": "Newly fetched and confirmed."}])
    result = fx.research_accept(work_root, batch, {"verdicts": {"t-a": answer}})

    record = fx.research_records(work_root)["t-a"]
    assert result["verified"] == ["t-a"] and result["resource_limited"] == []
    assert record["verification"]["findings"] == partial["findings"] + answer["findings"]
    assert record["attempts"][-2]["answer"] == partial
    assert record["attempts"][-1]["answer"] == answer
    assert fx.research_records(work_root)["t-sibling"] == sibling
    assert selected(work_root, "research_verify") == []
    before = canon.research_path(work_root, "de").read_bytes()
    replay = fx.research_accept(work_root, batch, {"verdicts": {"t-a": answer}})
    assert replay["verified"] == [] and replay["missing"] == ["t-a"]
    assert canon.research_path(work_root, "de").read_bytes() == before


def test_verification_cannot_complete_with_an_unchecked_source(work_root):
    partial_verification(work_root)
    batch = fx.build_verify_batch(work_root)
    item, = fx.packet_of(batch)["items"]
    before = canon.research_path(work_root, "de").read_bytes()
    result = fx.research_accept(work_root, batch, {"verdicts": {"t-a": fx.verdict_for(
        item, progress=COMPLETE, findings=[])}})
    assert result["verified"] == [] and result["invalid"][0]["id"] == "t-a"
    assert canon.research_path(work_root, "de").read_bytes() == before


def test_verification_continuation_cannot_replace_a_completed_check(work_root):
    partial_verification(work_root)
    batch = fx.build_verify_batch(work_root)
    item, = fx.packet_of(batch)["items"]
    reply = fx.verdict_for(item, progress=COMPLETE)  # tries changing earlier false to true
    fx.research_accept(work_root, batch, {"verdicts": {"t-a": reply}})
    checked = fx.research_records(work_root)["t-a"]["verification"]["findings"]
    assert checked[0]["confirmed"] is False and "Actually attempted" in checked[0]["detail"]


@pytest.mark.parametrize("progress", [None, {}, {"status": "pending", "remaining": []},
    {"status": "resource_limited", "remaining": []}, {"status": "complete", "remaining": ["Search"]},
    {"status": "resource_limited", "remaining": [""]}, {"status": "resource_limited", "remaining": "Search"}])
def test_malformed_progress_never_writes_a_record(work_root, progress):
    workspace(work_root, ("t-a",))
    result = fx.research_accept(work_root, fx.build_research_batch(work_root, ["t-a"]), {
        "terms": {"t-a": term(progress)}})
    assert result["stored"] == [] and result["invalid"][0]["id"] == "t-a"
    assert fx.research_records(work_root) == {}


@pytest.mark.parametrize("indexes", [[0, 0], [-1], [2], [True]])
def test_partial_verification_still_validates_source_indexes(work_root, indexes):
    partial_verification(work_root)
    batch = fx.build_verify_batch(work_root)
    item, = fx.packet_of(batch)["items"]
    answer = fx.verdict_for(item, progress=LIMITED, findings=[
        {"index": index, "confirmed": False, "detail": "unchecked"} for index in indexes])
    before = canon.research_path(work_root, "de").read_bytes()
    result = fx.research_accept(work_root, batch, {"verdicts": {"t-a": answer}})
    assert result["invalid"][0]["id"] == "t-a"
    assert canon.research_path(work_root, "de").read_bytes() == before


def test_legacy_record_is_archived_before_explicit_reresearch(work_root):
    workspace(work_root, ("t-a",))
    legacy = {"kind": "term", "source": "t-a", "research": fx.research_term(),
              "research_sha256": "legacy", "researched": {"run": "old", "at": "old"}, "verification": None}
    canon.save_research(work_root, "de", {"schema": 1, "locale": "de", "entries": {"t-a": legacy}})
    fx.research_accept(work_root, fx.build_research_batch(work_root, ["t-a"]), {"terms": {"t-a": term()}})
    assert fx.research_records(work_root)["t-a"]["attempts"][0] == {"kind": "legacy_snapshot", "record": legacy}
