"""Exercise the shipped extractor and signal builder on synthetic session logs."""
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
import weakref

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "cc-usage-coach" / "scripts"))
import extract as E
import lib_sessions as L
import signals as S


TURN_KEYS = set("s p d v m t ts in cr rd out c5 c1 wt nt".split())
SESSION_KEYS = set("""
    s base source_path p n_proj d models vers n_models n_versions n_turns n_side
    in cr rd out quota peak_ctx first_cr min_rd build_floor n_epochs n_model_epoch_groups
    n_read read_chars repeat_reads n_repeat_read_paths n_5m n_1h has_5m_writes
    n_comp n_err start end dur_min date
""".split())
PROJECT = "PRIVATE-project\ud800"
CWD = "/Users/example/" + PROJECT
READ_A = CWD + "/customer\ud800.py"
READ_B = CWD + "/customer\ud801.py"
CUSTOM_TOOL = "mcp__PRIVATE_CUSTOMER__secret_lookup"


def _assistant(uuid, minute=0, *, model="model-a", cwd=CWD, parent="root", agent=None,
               side=False, inp=0, creation=0, read=0, output=1, c5=0, c1=0, content=None):
    usage = {"input_tokens": inp, "cache_creation_input_tokens": creation,
             "cache_read_input_tokens": read, "output_tokens": output}
    if c5 or c1:
        usage["cache_creation"] = {"ephemeral_5m_input_tokens": c5,
                                   "ephemeral_1h_input_tokens": c1}
    return {"uuid": uuid, "parentUuid": parent, "agentId": agent, "isSidechain": side,
            "timestamp": f"2026-10-01T12:{minute:02d}:00Z", "cwd": cwd, "version": "test-version",
            "message": {"role": "assistant", "model": model, "usage": usage,
                        "content": content if content is not None else []}}


def _read(tuid, path):
    return {"type": "tool_use", "id": tuid, "name": "Read", "input": {"file_path": path}}


def _result(uuid, tuid, content):
    return {"uuid": uuid, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tuid, "content": content}]}}


def _write_log(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        fh.write("not valid JSON\n")
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def _isolate_environment(tmp_path, monkeypatch):
    home = tmp_path / "synthetic-home"
    out = tmp_path / "out"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CC_COACH_OUT", str(out))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CC_COACH_CONFIG_DIRS", raising=False)
    monkeypatch.setattr(E, "_SELF", {"example"})
    return home / ".claude" / "projects", out


def test_extract_emitted_dataset_reaches_signals(tmp_path, monkeypatch, capsys):
    projects, out = _isolate_environment(tmp_path, monkeypatch)
    primary_path = projects / "a-real" / "CUSTOMER-private.jsonl"
    child_path = projects / "a-real" / "subagents" / "child.jsonl"
    workflow_path = projects / "wf_test" / "workflow.jsonl"
    errors_path = projects / "z-errors" / "error-only.jsonl"

    failed = _assistant("retry", inp=999, creation=999, output=999)
    failed["isApiErrorMessage"] = True
    first = _assistant("retry", inp=10, creation=999, output=5, c5=100,
                       content=[None, "ignored block", _read("early-read", READ_A)])
    second = _assistant("retry", 1, agent="worker", side=True, inp=2, output=3, read=90, c1=20,
                        content=[_read("second-read", READ_A),
                                 {"type": "tool_use", "id": "custom", "name": CUSTOM_TOOL,
                                  "input": {"query": "private"}}])
    third = _assistant("third", 2, model="model-b", cwd="/Users/example/OTHER-private-project",
                       inp=3, creation=50, output=4, content=[_read("third-read", READ_B)])
    fourth = _assistant("fourth", 3, inp=1, output=2, c5=30,
                        content=[_read("fourth-read", READ_B)])
    # Same UUID, a different parent: this is a distinct composite dedup key.
    fifth = _assistant("retry", 4, parent="new-parent", inp=1, creation=999, read=30,
                       output=4, c5=5, c1=5)
    early_result = _result("early-result", "early-read", "abc")
    ignored = []
    for flag in ("isMeta", "isSnapshotUpdate", "isVisibleInTranscriptOnly"):
        entry = _assistant("ignored-" + flag, creation=999)
        entry[flag] = True
        entry["isCompactSummary"] = True
        ignored.append(entry)
    _write_log(primary_path, [None, [], 7, True, *ignored,
                             early_result, early_result, _result("early-custom", "custom", "private"),
                             failed, first, first, second,
                             _result("second-result", "second-read", [{"type": "text", "text": "xy"}]),
                             _result("orphan-result", "unknown-id", "unknown"), third,
                             {"uuid": "compact", "isCompactSummary": True}, fourth, fifth])
    _write_log(child_path, [_assistant("child", 6, side=True, output=2)])
    # Cross-file duplicate: only the new workflow turn contributes usage and tools.
    _write_log(workflow_path, [first, _assistant("workflow", 5)])
    _write_log(errors_path, [failed])

    # Use actual file discovery, with stable ownership of the cross-file duplicate.
    discover = L.discover_files
    assert set(discover()) == {str(p.resolve()) for p in
                               (primary_path, child_path, workflow_path, errors_path)}
    monkeypatch.setattr(L, "discover_files", lambda: sorted(discover()))
    assert E._run() is None
    dataset = str(out / "dataset")
    turns = list(S.stream_turns(dataset))
    sessions = S.load_sessions(dataset)
    tools = json.loads((out / "dataset" / "tools.json").read_text())
    meta = json.loads((out / "dataset" / "meta.json").read_text())

    assert len(turns) == 7
    assert len(sessions) == 3
    assert all(set(t) == TURN_KEYS for t in turns)
    assert all(set(s) == SESSION_KEYS for s in sessions)
    assert {s["d"] for s in sessions} == {"real", "workflow", "subagents"}
    by_path = {s["source_path"]: s for s in sessions}
    main = by_path[str(primary_path.resolve())]
    main_turns = [t for t in turns if t["s"] == main["s"]]
    assert [t["nt"] for t in main_turns] == [1, 2, 1, 1, 0]
    assert [t["wt"] for t in main_turns] == ["5m", "1h", None, "5m", "both"]
    assert [t["t"] for t in main_turns] == ["main", "side", "main", "main", "main"]
    assert {k: main[k] for k in ("n_turns", "n_side", "in", "cr", "rd", "out", "quota")} == {
        "n_turns": 5, "n_side": 1, "in": 17, "cr": 210, "rd": 120, "out": 18, "quota": 245}
    assert (main["build_floor"], main["n_epochs"], main["n_model_epoch_groups"]) == (200, 2, 3)
    assert (main["peak_ctx"], main["first_cr"], main["min_rd"]) == (110, 100, 30)
    assert (main["n_comp"], main["n_err"], main["n_proj"], main["n_models"]) == (1, 1, 2, 2)
    assert (main["n_5m"], main["n_1h"], main["dur_min"]) == (2, 1, 4.0)
    assert (main["n_read"], main["read_chars"], main["n_repeat_read_paths"]) == (4, 35, 2)
    expected_reads = sorted([[E._safe_leaf(p) + "#" +
                              hashlib.sha1(p.encode("utf-8", errors="surrogatepass")).hexdigest()[:6], 2]
                             for p in (READ_A, READ_B)])
    assert main["repeat_reads"] == expected_reads
    assert len({r[0].rsplit("#", 1)[1] for r in main["repeat_reads"]}) == 2
    workflow = by_path[str(workflow_path.resolve())]
    assert (workflow["n_turns"], workflow["build_floor"], workflow["n_epochs"],
            workflow["n_model_epoch_groups"]) == (1, 0, 1, 1)
    assert tools["tool_use_freq"] == {"Read": 4, CUSTOM_TOOL: 1}
    assert tools["tool_result_bytes"] == {
        "Read": {"chars": 35, "est_tokens": 8, "count": 2},
        CUSTOM_TOOL: {"chars": 7, "est_tokens": 1, "count": 1},
        "?": {"chars": 7, "est_tokens": 1, "count": 1}}
    assert meta["files"] == 4
    assert meta["totals"] == {"turns": 7, "in": 17, "cr": 210, "rd": 120, "out": 21,
                              "c5": 135, "c1": 25, "err": 2, "comp": 1, "sessions": 3,
                              "quota": 248}
    assert sum(s["n_err"] for s in sessions) == 1 < meta["totals"]["err"]

    # Consume emitted rows directly: the golden corpus never replaces this producer contract.
    pack = S.build_pack(sessions, S.stream_turns(dataset), tools)
    assert pack["schema_version"] == 4
    assert (pack["corpus"]["n_turns"], pack["corpus"]["quota"]) == (7, 248)
    assert (pack["corpus"]["n_err"], pack["corpus"]["error_turn_rate"]) == (1, 0.125)
    assert "retained dataset sessions" in pack["corpus"]["error_turn_note"]
    assert "files with no counted usage turns are omitted" in pack["corpus"]["error_turn_note"]
    assert set(pack["baselines_by_dir_class"]) == {"real", "workflow", "subagents"}
    candidate = next(c for c in pack["candidate_sessions"]["items"] if c["source_ref"] == main["s"])
    assert (candidate["n_err"], candidate["error_turn_rate"]) == (1, 0.1667)
    assert (candidate["d"], candidate["baseline_scope"]) == ("real", "real")
    assert candidate["read_evidence"]["repeat_reads"] == [
        ["file_" + r[0].rsplit("#", 1)[1], 2] for r in expected_reads]

    assert S.main() == 0
    published_text = (out / "signal_pack.json").read_text()
    published = json.loads(published_text)
    source_index = json.loads((out / "source_index.json").read_text())
    project_index = json.loads((out / "project_index.json").read_text())
    tool_index = json.loads((out / "tool_index.json").read_text())
    assert source_index == {s["s"]: s["source_path"] for s in sessions}
    assert str(errors_path.resolve()) not in source_index.values()
    assert set(project_index.values()) == {PROJECT, "OTHER-private-project"}
    assert CUSTOM_TOOL in tool_index.values()
    assert "Read" not in tool_index.values()
    assert all(re.fullmatch(r"tool_[0-9a-f]{10}", key) for key in tool_index)
    assert all(re.fullmatch(r"proj_[0-9a-f]{10}", row["p"])
               for row in published["pareto"]["top_projects"])
    assert all(re.fullmatch(r"sess_[0-9a-f]{10}", row["source_ref"])
               for row in published["candidate_sessions"]["items"])
    for needle in ("PRIVATE-project", "OTHER-private-project", "CUSTOMER-private", CUSTOM_TOOL,
                   "customer", "/Users/example", str(primary_path)):
        assert needle not in published_text
    for path in list((out / "dataset").iterdir()) + [out / f for f in
                                                    ("source_index.json", "project_index.json", "tool_index.json")]:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    captured = capsys.readouterr()
    assert captured.err == ""
    for needle in (str(tmp_path), "PRIVATE-project", CUSTOM_TOOL, "CUSTOMER-private"):
        assert needle not in captured.out


def test_extract_releases_transient_entries_between_streamed_turns(tmp_path, monkeypatch):
    _, out = _isolate_environment(tmp_path, monkeypatch)
    monkeypatch.setattr(L, "discover_files", lambda: ["/synthetic/session.jsonl"])

    class Entry(dict):
        pass

    refs = []
    peak_live = 0
    passes = 0

    def entries(path):
        nonlocal passes, peak_live
        assert path == "/synthetic/session.jsonl"
        passes += 1
        for i in range(200):
            # Ordinary independent parsed dicts, with no cycles or external owner.
            entry = Entry(_assistant(None, creation=10, content=[
                {"type": "text", "text": f"payload-{i}:" + "x" * 4096}]))
            refs.append(weakref.ref(entry))
            peak_live = max(peak_live, sum(ref() is not None for ref in refs))
            yield entry
            del entry

    monkeypatch.setattr(L, "iter_entries", entries)
    E._run()
    assert passes == 2
    assert peak_live <= 3, f"retained {peak_live} parsed entries from a 200-entry stream"
    assert all(ref() is None for ref in refs)
    session, = S.load_sessions(str(out / "dataset"))
    assert (session["n_turns"], session["build_floor"], session["n_epochs"]) == (200, 10, 1)


def test_segmented_floor_handles_generators_empty_and_zero_epochs():
    assert E.segmented_build_floor(iter(())) == (0, 0, 0)
    assert E.segmented_build_floor(iter([{"boundary": True, "model": "a", "ctx": 0}])) == (0, 1, 1)
    rows = [(False, "a", 100), (False, "a", 90), (False, "b", 40),
            (True, "a", 20), (False, "a", 30), (False, "b", 0), (True, "a", 10)]
    stream = ({"boundary": boundary, "model": model, "ctx": ctx} for boundary, model, ctx in rows)
    assert E.segmented_build_floor(stream) == (180, 3, 5)
