"""
Tests for the signals.py session_length block (schema_version 2's new top-level key)
plus the cross-cutting invariants that must hold for the frozen fixture.

Covers, on the frozen fixture: all 13 required keys (incl. session_length); by_dir_class
sums to corpus.n_sessions; real_turns.histogram counts sum to by_dir_class.real;
non-null percentiles monotone (p50<=p90<=p99<=max); real_with_side_turns <= real.
Plus degenerate cases on synthetic corpora: empty corpus, all-side corpus, all-dur-None
reals — none may raise, and stats collapse to None as designed. session_length must be
identical under reversed input.

Run: python3 -m pytest tests/test_signals.py -q
"""
import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "cc-usage-coach", "scripts"))
import signals as S

# Reuse the frozen corpus builders (synthetic, neutral) from the golden harness.
import test_golden as G

FIXTURE_DIR = os.path.join(HERE, "fixtures", "frozen_dataset")


def _load():
    sessions = S.load_sessions(FIXTURE_DIR)
    turns = list(S.stream_turns(FIXTURE_DIR))
    with open(os.path.join(FIXTURE_DIR, "tools.json")) as fh:
        tools = json.load(fh)
    return sessions, turns, tools


def _empty_tools():
    return {"tool_result_bytes": {}, "tool_use_freq": {}}


# --- synthetic session builder (matches extract.py schema) -------------------
def sess(s, d="real", n_turns=10, n_side=0, dur_min=60.0):
    """Minimal synthetic session for degenerate-path tests (project label always synthetic)."""
    return G._session(s, "project-a", d=d, n_turns=n_turns, n_side=n_side, dur_min=dur_min)


# --- 1. frozen-fixture invariants -------------------------------------------
def test_fixture_has_13_keys_incl_session_length():
    sessions, turns, tools = _load()
    pack = S.build_pack(sessions, iter(turns), tools)
    assert pack["schema_version"] == 3
    assert "session_length" in pack
    assert len(pack) == 13


def test_by_dir_class_sums_to_n_sessions():
    sessions, turns, tools = _load()
    pack = S.build_pack(sessions, iter(turns), tools)
    bdc = pack["session_length"]["by_dir_class"]
    assert set(bdc) == {"real", "subagents", "workflow"}
    assert bdc["real"] + bdc["subagents"] + bdc["workflow"] == pack["corpus"]["n_sessions"]


def test_histogram_sums_to_real_count():
    sessions, turns, tools = _load()
    sl = S.build_pack(sessions, iter(turns), tools)["session_length"]
    hist = sl["real_turns"]["histogram"]
    assert sum(hist.values()) == sl["by_dir_class"]["real"]
    # buckets are exactly the SESSION_LENGTH_BUCKETS labels
    assert set(hist) == {label for _, _, label in S.SESSION_LENGTH_BUCKETS}


def test_real_turns_percentiles_monotone():
    sessions, turns, tools = _load()
    rt = S.build_pack(sessions, iter(turns), tools)["session_length"]["real_turns"]
    assert rt["p50"] is not None
    assert rt["p50"] <= rt["p90"] <= rt["p99"] <= rt["max"]
    assert rt["mean"] is not None


def test_real_with_side_turns_le_real():
    sessions, turns, tools = _load()
    sl = S.build_pack(sessions, iter(turns), tools)["session_length"]
    assert sl["real_with_side_turns"] <= sl["by_dir_class"]["real"]
    assert sl["real_with_side_turns"] >= 1   # fixture has an inline-fanout real session


def test_real_dur_min_present_and_noted():
    sessions, turns, tools = _load()
    rd = S.build_pack(sessions, iter(turns), tools)["session_length"]["real_dur_min"]
    assert set(rd) == {"p50", "p90", "max", "note"}
    assert rd["p50"] is not None and rd["p50"] <= rd["p90"] <= rd["max"]


def test_session_length_identical_under_reversed_input():
    sessions, turns, tools = _load()
    a = S.build_pack(sessions, iter(turns), tools)["session_length"]
    b = S.build_pack(list(reversed(sessions)), iter(list(reversed(turns))), tools)["session_length"]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# --- 2. degenerate cases (must not raise; stats collapse to None) -----------
def test_empty_corpus_all_none_no_raise():
    pack = S.build_pack([], iter([]), _empty_tools())
    sl = pack["session_length"]
    assert sl["by_dir_class"] == {"real": 0, "subagents": 0, "workflow": 0}
    assert sl["real_with_side_turns"] == 0
    rt = sl["real_turns"]
    assert rt["p50"] is None and rt["p90"] is None and rt["p99"] is None
    assert rt["max"] is None and rt["mean"] is None
    assert sum(rt["histogram"].values()) == 0
    rd = sl["real_dur_min"]
    assert rd["p50"] is None and rd["p90"] is None and rd["max"] is None


def test_all_side_corpus_real_turns_all_none():
    """A corpus with only subagents/workflow sessions (no `real`) -> real_turns all-None."""
    sessions = ([sess(f"sub{i}", d="subagents", n_turns=4) for i in range(5)] +
                [sess(f"wf{i}", d="workflow", n_turns=7) for i in range(3)])
    pack = S.build_pack(sessions, iter([]), _empty_tools())
    sl = pack["session_length"]
    assert sl["by_dir_class"] == {"real": 0, "subagents": 5, "workflow": 3}
    rt = sl["real_turns"]
    assert rt["p50"] is None and rt["p90"] is None and rt["p99"] is None
    assert rt["max"] is None and rt["mean"] is None
    assert sum(rt["histogram"].values()) == 0
    assert sl["real_with_side_turns"] == 0


def test_all_dur_none_reals_dur_dist_all_none():
    """Real sessions where every dur_min is None -> real_dur_min p50/p90/max all None, no raise."""
    sessions = [sess(f"r{i}", d="real", n_turns=10 + i, dur_min=None) for i in range(6)]
    pack = S.build_pack(sessions, iter([]), _empty_tools())
    sl = pack["session_length"]
    assert sl["by_dir_class"]["real"] == 6
    rd = sl["real_dur_min"]
    assert rd["p50"] is None and rd["p90"] is None and rd["max"] is None
    # real_turns is still populated (only durations are missing)
    rt = sl["real_turns"]
    assert rt["p50"] is not None and sum(rt["histogram"].values()) == 6


# --- 3. first_ts None handling (codex r2 MEDIUM: None-vs-str comparison) ------
def test_build_pack_handles_first_ts_none_no_crash():
    """A (version, model) group whose FIRST main turn has ts=None, with a later real-ts turn,
    must not raise on the None-vs-str comparison — and first_ts handling stays order-stable."""
    def turn(ts):
        return {"s": "r0", "p": "project-a", "d": "real", "v": "2.1.1", "m": "claude-x",
                "t": "main", "ts": ts, "in": 1, "cr": 10, "rd": 5, "out": 2,
                "c5": 0, "c1": 0, "wt": "5m", "nt": 0}
    turns = [turn(None), turn("2026-01-02T00:00:00Z"), turn("2026-01-01T00:00:00Z")]
    sessions = [sess("r0", d="real", n_turns=3)]
    pack = S.build_pack(sessions, iter(turns), _empty_tools())          # must not raise
    assert any(row["v"] == "2.1.1" for row in pack["version_signals_by_model"])
    rev = S.build_pack(sessions, iter(list(reversed(turns))), _empty_tools())
    assert json.dumps(pack["version_signals_by_model"], sort_keys=True) == \
           json.dumps(rev["version_signals_by_model"], sort_keys=True)


# --- 4. source_index.json hardened write (codex r2: 0600 + O_NOFOLLOW + error path) ----
def _seed_dataset(out_dir):
    import shutil
    ds = os.path.join(out_dir, "dataset")
    os.makedirs(ds, exist_ok=True)
    for fn in ("sessions.jsonl", "turns.jsonl", "tools.json"):
        shutil.copy(os.path.join(FIXTURE_DIR, fn), os.path.join(ds, fn))


def test_main_writes_source_index_0600(tmp_path, monkeypatch):
    out = tmp_path / "out"
    _seed_dataset(str(out))
    monkeypatch.setenv("CC_COACH_OUT", str(out))
    assert S.main() == 0
    idx = out / "source_index.json"
    assert idx.exists()
    assert (os.stat(idx).st_mode & 0o777) == 0o600          # created/forced 0600, no umask window
    # assert REAL content was written — an empty / early-return file must NOT pass (codex r3 check 5)
    seeded = S.load_sessions(os.path.join(str(out), "dataset"))
    # source_index is keyed by the OPAQUE _source_ref (derived from source_path), matching the pack's
    # source_ref so arc.py resolves — NOT by the dataset's raw `s`.
    expected = {S._source_ref(s): s["source_path"] for s in seeded if s.get("source_path")}
    assert expected, "fixture must seed at least one source_path for this to assert real content"
    assert json.loads(idx.read_text()) == expected
    assert (out / "signal_pack.json").exists()


def test_main_refuses_symlinked_source_index(tmp_path, monkeypatch, capsys):
    if not hasattr(os, "O_NOFOLLOW"):
        import pytest
        pytest.skip("O_NOFOLLOW unavailable on this platform")
    out = tmp_path / "out"
    _seed_dataset(str(out))
    target = tmp_path / "evil_target"
    target.write_text("{}")
    os.symlink(str(target), str(out / "source_index.json"))   # plant a symlink at the index path
    monkeypatch.setenv("CC_COACH_OUT", str(out))
    rc = S.main()
    err = capsys.readouterr().err
    assert rc == 1                                            # O_NOFOLLOW refuses; main bails non-zero
    assert "could not" in err                                # generic message...
    assert "/Users/" not in err and str(out) not in err      # ...with NO absolute path leaked
    assert target.read_text() == "{}"                        # symlink target was NOT written through


# --- 5. project anonymization: shareable pack has opaque project IDs; real names only in the
#     LOCAL-ONLY project_index.json (PR #1 review) ----------------------------------------------
def test_main_writes_project_index_local_only(tmp_path, monkeypatch):
    import re
    out = tmp_path / "out"
    _seed_dataset(str(out))
    monkeypatch.setenv("CC_COACH_OUT", str(out))
    assert S.main() == 0
    pidx = out / "project_index.json"
    assert pidx.exists()
    assert (os.stat(pidx).st_mode & 0o777) == 0o600          # LOCAL-ONLY, 0600 like source_index
    proj_map = json.loads(pidx.read_text())
    assert proj_map, "fixture has real projects -> the id->name map must be non-empty"
    # each key is an opaque id that resolves (via _proj_id) to its real-name value
    for pid, name in proj_map.items():
        assert re.match(r"^proj_[0-9a-f]{10}$", pid) and S._proj_id(name) == pid
    # the shareable pack on disk carries ONLY opaque/sentinel project labels, and no real name
    pack = json.loads((out / "signal_pack.json").read_text())
    labels = ([tp["p"] for tp in pack["pareto"]["top_projects"]] +
              [it["p"] for it in pack["candidate_sessions"]["items"]])
    for lab in labels:
        assert re.match(r"^proj_[0-9a-f]{10}$", lab)        # EVERY project label is opaque
    # exclude CC structural dir-class labels (they appear as by_dir_class keys, not project names)
    blob = json.dumps(pack)
    for name in set(proj_map.values()) - {"real", "subagents", "workflow"}:
        assert name not in blob, f"real project name leaked into shareable pack: {name!r}"


# --- 6. source_ref is derived from the realpath, never trusted from a (possibly stale) dataset ---
def test_source_ref_opaque_even_for_stale_filename_s():
    """signals derives the pack's source_ref from the authoritative source_path, NOT the dataset's
    stored `s` — so a stale/pre-fix sessions.jsonl whose `s` still carries a filename cannot leak it
    into the shareable pack. Re-derives the SAME id extract assigns, so source_index resolves. (F1.)"""
    import re
    stale = {"s": "CLIENTACME-debugging-abc123",
             "source_path": "/Users/x/p/CLIENTACME-debugging.jsonl"}
    ref = S._source_ref(stale)
    assert re.match(r"^sess_[0-9a-f]{10}$", ref)
    assert "CLIENTACME" not in ref and "debugging" not in ref
    assert ref == S.L.session_id("/Users/x/p/CLIENTACME-debugging.jsonl")
    # with no source_path, it still yields an opaque token (hashes the stale s, never echoes it)
    ref2 = S._source_ref({"s": "CLIENTACME-debugging-abc123"})
    assert re.match(r"^sess_[0-9a-f]{10}$", ref2) and "CLIENTACME" not in ref2
