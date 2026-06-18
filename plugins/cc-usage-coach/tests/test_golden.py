"""
Deterministic golden proof for signals.build_pack over a FROZEN synthetic dataset.

Loads tests/fixtures/frozen_dataset/{sessions,turns}.jsonl + tools.json exactly the
way signals.main() loads the live dataset (load_sessions + stream_turns + json.load),
runs build_pack, and asserts the result equals the checked-in golden_pack.json EXACTLY.
Plus structural invariants that hold for ANY legitimate signals.py tweak: all required
keys present, the session_length dir-class counts sum to the corpus, recache_share in
[0,1], NO filesystem-path / source leakage, and order-independence (reversed inputs ->
identical pack).

The fixture corpus is fully synthetic and NEUTRAL — synthetic identities (/Users/x/...),
project labels project-a/b/c + a subagents + a workflow id. No real names ship here.

Run:    python3 -m pytest tests/test_golden.py -q
Re-pin: python3 tests/test_golden.py --update   # regenerate fixtures + golden_pack.json
"""
import os, sys, json, re, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "cc-usage-coach", "scripts"))
import signals as S

FIXTURE_DIR = os.path.join(HERE, "fixtures", "frozen_dataset")
GOLDEN_PATH = os.path.join(HERE, "fixtures", "golden_pack.json")

REQUIRED_KEYS = (
    "schema_version", "corpus", "split", "pareto", "baselines", "session_length",
    "candidate_sessions", "fan_out", "version_signals_by_model", "tool_injection",
    "behavior_color", "model_task_fit_signals", "currency_notes",
)
PATH_NEEDLES = ("/Users/", "/home/", "-Users-", "-home-", ".jsonl", "source_path")
# synthetic project labels that MUST be anonymized out of the shareable (shipped) pack
PROJECT_NEEDLES = ("project-a", "project-b", "project-c")


# --------------------------------------------------------------------------- #
# Frozen corpus — the single source of truth for the fixture files + golden.
# Field schema mirrors extract.py's emitted session/turn rows. Hand-crafted so
# build_pack exercises, deterministically:
#   * three projects (project-a/b/c) + a subagents dir + a workflow dir
#   * real sessions with VARIED n_turns (1, 3, 8, 25, 120, +fillers) so the
#     session_length histogram + percentiles are non-trivial
#   * a real session with n_side>0 (inline fan-out)        -> real_with_side_turns
#   * a real session with dur_min=None (degenerate dur path)
#   * subagents (incl. a trivial <=2-turn sub) + a workflow side-thread
#   * a multi-epoch/multi-model session (build_floor > peak_ctx)
#   * a repeat-read session + a zero-read session
#   * >=2 versions of one model with n>=MIN_SLICE each (opus 2.1.150 -> 2.1.158)
#   * >=30 sessions so insufficient_data is False and baselines populate
# Only synthetic identities are ever written. NEUTRAL — ships publicly.
# --------------------------------------------------------------------------- #
def _sid(name):
    """Opaque session id, mirroring extract._session_id's shape (sess_<hex>). The fixture's
    readable session name lives in `base` (LOCAL-ONLY); the pack's source_ref is this hash."""
    return "sess_" + hashlib.sha1(str(name).encode()).hexdigest()[:10]


def _session(s, p, d="real", n_turns=10, cr=200_000, peak_ctx=100_000, build_floor=None,
             n_comp=0, n_models=1, n_side=0, n_read=0, read_chars=0, repeat_reads=None,
             n_repeat_read_paths=0, first_cr=50_000, date="2026-06-01", n_epochs=1,
             models=None, vers=None, inp=10_000, rd=500_000, out=0, dur_min=60.0,
             source_path=None):
    quota = inp + cr + out
    start = date + "T08:00:00.000Z" if date else None
    end = date + "T09:00:00.000Z" if date else None
    return {
        "s": _sid(s), "base": s, "source_path": source_path or f"/Users/x/project-a/{s}.jsonl",
        "p": p, "n_proj": 1, "d": d, "models": models or (["m"] * n_models), "vers": vers or ["1"],
        "n_models": n_models, "n_versions": len(vers) if vers else 1, "n_turns": n_turns, "n_side": n_side,
        "in": inp, "cr": cr, "rd": rd, "out": out, "quota": quota,
        "peak_ctx": peak_ctx, "first_cr": first_cr, "min_rd": 1000,
        "build_floor": build_floor if build_floor is not None else peak_ctx,
        "n_epochs": n_epochs, "n_model_epoch_groups": n_models,
        "n_read": n_read, "read_chars": read_chars, "repeat_reads": repeat_reads or [],
        "n_repeat_read_paths": n_repeat_read_paths,
        "n_5m": 0, "n_1h": n_turns, "has_5m_writes": False, "n_comp": n_comp, "n_err": 0,
        "start": start, "end": end, "dur_min": dur_min, "date": date,
    }


def _turn(s, p, m="m", t="main", cr=20_000, inp=100, out=500, rd=50_000, nt=1,
          v="1", ts="2026-06-01T08:00:00.000Z"):
    return {"s": _sid(s), "p": p, "d": "real", "v": v, "m": m, "t": t, "ts": ts,
            "in": inp, "cr": cr, "rd": rd, "out": out, "c5": 0, "c1": cr, "wt": "1h", "nt": nt}


def build_corpus():
    """Return (sessions, turns, tools) for the frozen fixture. Pure + deterministic."""
    sessions, turns = [], []

    def add(sess, sess_turns):
        sessions.append(sess)
        turns.extend(sess_turns)

    # 1. Big main session -> top cost bucket; n_turns=120 (101-300 histogram bucket).
    add(_session("a-big", "project-a", n_turns=120, cr=4_000_000, peak_ctx=400_000,
                 build_floor=400_000, first_cr=80_000, date="2026-06-02", out=20_000, inp=40_000),
        [_turn("a-big", "project-a", m="opus", v="2.1.150", cr=100_000, inp=1000, out=500,
               ts="2026-06-02T08:00:00.000Z") for _ in range(120)])

    # 2. Multi-epoch (compaction) + multi-model session: floor sums per epoch x model,
    #    so build_floor (900k) > single peak_ctx (300k). n_turns=25, n_side=4 -> inline fan-out.
    add(_session("a-marathon", "project-a", n_turns=25, cr=2_400_000, peak_ctx=300_000,
                 build_floor=900_000, n_comp=1, n_models=2, n_epochs=2, n_side=4, first_cr=70_000,
                 date="2026-06-03", models=["opus", "sonnet"], vers=["2.1.150", "2.1.158"],
                 out=15_000, inp=30_000),
        ([_turn("a-marathon", "project-a", m="opus", v="2.1.150", cr=80_000,
                ts="2026-06-03T08:00:00.000Z") for _ in range(13)] +
         [_turn("a-marathon", "project-a", m="sonnet", v="2.1.158", cr=80_000,
                ts="2026-06-03T09:00:00.000Z") for _ in range(12)]))

    # 3. Repeat-read session -> repeat_reads (basename#hash) + why:["repeat_read"]. n_turns=8.
    add(_session("b-repeat", "project-b", n_turns=8, cr=300_000, peak_ctx=150_000, build_floor=150_000,
                 n_read=9, read_chars=180_000, repeat_reads=[["worker.py#a1b2c3", 3], ["config.json#d4e5f6", 2]],
                 n_repeat_read_paths=2, date="2026-06-04", out=3000, inp=12_000),
        [_turn("b-repeat", "project-b", cr=25_000, ts="2026-06-04T08:00:00.000Z") for _ in range(8)])

    # 4. Zero-read session -> stable empty read_evidence. n_turns=3 (2-5 bucket).
    add(_session("b-zero", "project-b", n_turns=3, cr=160_000, peak_ctx=90_000, build_floor=90_000,
                 n_read=0, read_chars=0, date="2026-06-04", out=2000, inp=8000),
        [_turn("b-zero", "project-b", cr=20_000, ts="2026-06-04T10:00:00.000Z") for _ in range(3)])

    # 5. Degenerate-duration real session: dur_min=None (single timestamp / unparseable span).
    #    n_turns=1 (the "1" histogram bucket).
    add(_session("c-onesh", "project-c", n_turns=1, cr=120_000, peak_ctx=70_000, build_floor=70_000,
                 date="2026-06-04", out=1000, inp=6000, dur_min=None),
        [_turn("c-onesh", "project-c", cr=120_000, ts="2026-06-04T12:00:00.000Z")])

    # 6. Subagents dirclass: one trivial (<=2 turns) + one larger -> fan_out + trivial rate.
    add(_session("sub-trivial", "subagents", d="subagents", n_turns=2, cr=120_000, peak_ctx=60_000,
                 build_floor=60_000, n_side=2, first_cr=54_000, date="2026-06-05", out=1000, inp=5000,
                 source_path="/Users/x/subagents/sub-trivial.jsonl"),
        [_turn("sub-trivial", "subagents", t="side", cr=60_000, ts="2026-06-05T08:00:00.000Z")
         for _ in range(2)])
    add(_session("sub-larger", "subagents", d="subagents", n_turns=6, cr=300_000, peak_ctx=80_000,
                 build_floor=80_000, n_side=3, first_cr=55_000, date="2026-06-05", out=2000, inp=6000,
                 source_path="/Users/x/subagents/sub-larger.jsonl"),
        [_turn("sub-larger", "subagents", t="side", cr=50_000, ts="2026-06-05T09:00:00.000Z")
         for _ in range(6)])

    # 7. Workflow dirclass -> workflow_share. Synthetic workflow id label.
    add(_session("wf-001", "wf_001", d="workflow", n_turns=10, cr=400_000, peak_ctx=120_000,
                 build_floor=120_000, n_side=4, first_cr=60_000, date="2026-06-06", out=4000, inp=10_000,
                 source_path="/Users/x/wf_001/wf-001.jsonl"),
        [_turn("wf-001", "wf_001", t="side", cr=40_000, ts="2026-06-06T08:00:00.000Z") for _ in range(10)])

    # 8. Version churn: opus 2.1.150 (low cr/turn) then 2.1.158 (high cr/turn), each n>=MIN_SLICE,
    #    so ratio_vs_trailing_median on 2.1.158 is > 1 and reuse_ratio differs across versions.
    #    n_turns=60 (51-100 bucket).
    add(_session("c-canary", "project-c", n_turns=60, cr=3_000_000, peak_ctx=200_000,
                 build_floor=200_000, n_models=1, models=["opus"], vers=["2.1.150", "2.1.158"],
                 date="2026-06-07", out=10_000, inp=30_000),
        ([_turn("c-canary", "project-c", m="opus", v="2.1.150", cr=20_000,
                ts="2026-06-07T08:00:00.000Z") for _ in range(30)] +
         [_turn("c-canary", "project-c", m="opus", v="2.1.158", cr=80_000,
                ts="2026-06-08T08:00:00.000Z") for _ in range(30)]))

    # 9. Ordinary filler across the three projects -> reach >=30 sessions, populate baselines.
    #    Varied n_turns so percentiles are non-degenerate.
    projs = ["project-a", "project-b", "project-c"]
    i = 0
    while len(sessions) < 34:
        pj = projs[i % len(projs)]
        nt = 10 + (i % 5)
        add(_session(f"fill-{i:02d}", pj, n_turns=nt, cr=200_000 + i * 1000, peak_ctx=100_000,
                     build_floor=100_000, date="2026-06-09", out=1000, inp=10_000),
            [_turn(f"fill-{i:02d}", pj, cr=20_000, ts="2026-06-09T08:00:00.000Z") for _ in range(nt)])
        i += 1

    # tools.json: dict insertion order is load-bearing (by_tool keeps list(trb.items())[:12]).
    tools = {
        "tool_use_freq": {"Bash": 500, "Read": 300, "Edit": 120, "Agent": 40, "Write": 30},
        "tool_result_bytes": {
            "Read": {"chars": 1_200_000, "est_tokens": 300_000, "count": 300},
            "Bash": {"chars": 800_000, "est_tokens": 200_000, "count": 500},
            "Edit": {"chars": 240_000, "est_tokens": 60_000, "count": 120},
            "Agent": {"chars": 400_000, "est_tokens": 100_000, "count": 40},
        },
    }
    return sessions, turns, tools


# --------------------------------------------------------------------------- #
# Fixture IO — identical load path to signals.main().
# --------------------------------------------------------------------------- #
def write_fixture(sessions, turns, tools):
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(os.path.join(FIXTURE_DIR, "sessions.jsonl"), "w") as fh:
        for s in sessions:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    with open(os.path.join(FIXTURE_DIR, "turns.jsonl"), "w") as fh:
        for t in turns:
            fh.write(json.dumps(t, sort_keys=True) + "\n")
    with open(os.path.join(FIXTURE_DIR, "tools.json"), "w") as fh:
        json.dump(tools, fh, indent=2)
        fh.write("\n")


def load_fixture():
    """Load the checked-in fixture exactly the way signals.main() loads the live dataset."""
    sessions = S.load_sessions(FIXTURE_DIR)
    turns = list(S.stream_turns(FIXTURE_DIR))
    with open(os.path.join(FIXTURE_DIR, "tools.json")) as fh:
        tools = json.load(fh)
    return sessions, turns, tools


def canon(obj):
    """Canonical JSON string for exact structural comparison."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def shipped_pack(sessions, turns, tools):
    """The artifact actually written to signal_pack.json: build_pack then project anonymization.
    The golden + safety/determinism proofs run on THIS (what ships), not the raw real-name pack."""
    return S.anonymize_projects(S.build_pack(sessions, iter(turns), tools))[0]


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_golden_pack_exact_match():
    """The shipped pack (build_pack + project anonymization) over the frozen fixture equals the
    committed golden_pack.json exactly."""
    sessions, turns, tools = load_fixture()
    pack = shipped_pack(sessions, turns, tools)
    with open(GOLDEN_PATH) as fh:
        golden = json.load(fh)
    assert canon(pack) == canon(golden), (
        "signal pack != committed golden_pack.json. If a signals.py change is intentional, "
        "re-pin with: python3 tests/test_golden.py --update"
    )


def test_all_required_keys_present():
    sessions, turns, tools = load_fixture()
    pack = shipped_pack(sessions, turns, tools)
    assert pack["schema_version"] == 3
    for k in REQUIRED_KEYS:
        assert k in pack, f"missing top-level key: {k}"
    assert len(REQUIRED_KEYS) == 13


def test_dir_class_counts_sum_to_corpus():
    sessions, turns, tools = load_fixture()
    pack = S.build_pack(sessions, iter(turns), tools)
    bdc = pack["session_length"]["by_dir_class"]
    assert bdc["real"] + bdc["subagents"] + bdc["workflow"] == pack["corpus"]["n_sessions"]


def test_recache_share_in_unit_interval():
    sessions, turns, tools = load_fixture()
    pack = S.build_pack(sessions, iter(turns), tools)
    rs = pack["corpus"]["recache_share"]
    assert 0.0 <= rs <= 1.0


def test_no_path_leakage_in_pack():
    sessions, turns, tools = load_fixture()
    pack = shipped_pack(sessions, turns, tools)
    blob = json.dumps(pack)
    for needle in PATH_NEEDLES + PROJECT_NEEDLES:
        assert needle not in blob, f"path/source/project leak: {needle!r} present in shareable pack"


def test_project_labels_opaque_and_mapped():
    """Shipped pack carries only opaque project IDs (or sentinels); the LOCAL-ONLY map resolves
    each back to a real name; and no real project name leaks into the shareable pack."""
    sessions, turns, tools = load_fixture()
    raw = S.build_pack(sessions, iter(turns), tools)
    shipped, proj_map = S.anonymize_projects(raw)
    opaque = re.compile(r"^proj_[0-9a-f]{10}$")
    labels = ([tp["p"] for tp in shipped["pareto"]["top_projects"]] +
              [it["p"] for it in shipped["candidate_sessions"]["items"]])
    assert labels, "fixture must produce project labels to anonymize"
    for lab in labels:
        assert opaque.match(lab), f"non-opaque project label: {lab!r}"   # EVERY label is hashed
    # the map is the inverse of _proj_id and resolves every opaque id to a real name
    for pid, name in proj_map.items():
        assert opaque.match(pid) and S._proj_id(name) == pid
    # every real label in the raw pack is recoverable via the local map, and none leaks into shipped
    raw_names = {tp["p"] for tp in raw["pareto"]["top_projects"]}
    assert raw_names, "fixture should have real project names in top_projects"
    assert raw_names <= set(proj_map.values()), "every real project label must be in the local map"
    # dir-class labels (real/subagents/workflow) legitimately appear as session_length.by_dir_class
    # KEYS — CC structural vocabulary, not user project names — so exclude them from the leak check.
    blob = json.dumps(shipped)
    for name in raw_names - {"real", "subagents", "workflow"}:
        assert name not in blob, f"real project name leaked into shareable pack: {name!r}"


def test_every_label_hashed_no_passthrough():
    """A real project literally named like a former fallback ("unknown"/"?") must be HASHED, not
    passed through unmasked; a non-string label is coerced, never crashes. (codex review.)"""
    pack = {"pareto": {"top_projects": [{"p": "unknown", "quota": 1}]},
            "candidate_sessions": {"items": [{"p": "?"}]}}
    anon, mapping = S.anonymize_projects(pack)
    opaque = re.compile(r"^proj_[0-9a-f]{10}$")
    assert opaque.match(anon["pareto"]["top_projects"][0]["p"])
    assert opaque.match(anon["candidate_sessions"]["items"][0]["p"])
    assert set(mapping.values()) == {"unknown", "?"}      # both recorded for LOCAL-ONLY resolution
    assert opaque.match(S._proj_id(None))                 # non-string coerced -> no crash


def test_repeat_reads_filenames_opaque_in_shipped_pack():
    """repeat_reads in the shareable pack carries OPAQUE file tokens (`file_<hash>`), never real
    filenames — a filename can embed a project/client/sensitive name (found on real data: project
    names appeared inside re-read filenames). The repeat count is preserved. (codex/PR review.)"""
    sessions, turns, tools = load_fixture()
    shipped = shipped_pack(sessions, turns, tools)
    tok = re.compile(r"^file_[0-9a-f]+$")
    seen = False
    for it in shipped["candidate_sessions"]["items"]:
        for entry in it["read_evidence"]["repeat_reads"]:
            seen = True
            assert tok.match(entry[0]), f"non-opaque repeat_read token: {entry[0]!r}"
            assert isinstance(entry[1], int)
    assert seen, "fixture must have a candidate with repeat_reads to exercise this"
    blob = json.dumps(shipped)                       # synthetic fixture filenames must be gone
    assert "worker.py" not in blob and "config.json" not in blob


def test_read_token_no_filename_passthrough():
    """_read_token keeps only a valid trailing hex hash; any other shape (no `#`, non-hex tail,
    non-string, empty) is hashed whole, never emitted verbatim — so a malformed/corrupt dataset
    entry can never leak a filename into the shareable pack. (codex review of the PR delta.)"""
    tok = re.compile(r"^file_[0-9a-f]+$")
    # well-formed `leaf#hash`: keep the hash, drop the leaf
    assert S._read_token("worker.py#a1b2c3") == "file_a1b2c3"
    # filename containing '#' but still ending in `#<hex>`: still only the trailing hash survives
    assert S._read_token("my#project.py#d4e5f6") == "file_d4e5f6"
    # the trailing hex is trusted ONLY after a real `#`: a bare hex string (no `#`) is hashed whole,
    # never echoed -> differs from the same hex used as a `#`-delimited tail. (codex review.)
    assert S._read_token("a1b2c3") != S._read_token("x#a1b2c3")
    assert S._read_token("123") != "file_123"
    # malformed shapes must NOT pass the original through; they get hashed and match the token shape
    for bad in ("orphan_a1b2c3", "client-project.py", "report#NOTHEX", "secret.py#", "",
                "a#b#client", "a1b2c3", "DEADBEEF"):
        out = S._read_token(bad)
        assert tok.match(out), f"non-opaque token for {bad!r}: {out!r}"
        for needle in ("client", "project", "secret", "orphan"):
            assert needle not in out
    # non-string entries are coerced + hashed, never emitted as `file_None` / `file_123` / `file_['x']`
    for bad in (None, 123, ["client.py"]):
        out = S._read_token(bad)
        assert tok.match(out), f"non-opaque token for {bad!r}: {out!r}"
        assert "None" not in out and "client" not in out
    # a lone surrogate (real filesystems surrogate-escape undecodable filename bytes) must hash, not
    # raise UnicodeEncodeError, on either the tail or the whole-string fallback. (codex review.)
    assert tok.match(S._read_token("\ud800"))
    assert tok.match(S._read_token("\ud800#zz"))


def test_source_ref_opaque_and_base_dropped():
    """Every candidate session in the shipped pack carries an OPAQUE sess_<hash> source_ref, and the
    raw filename stem `base` (which can embed a project/client name or a username) is dropped — the
    real basename stays LOCAL-ONLY in the dataset. No fixture session's base leaks into the shareable
    pack. (PR audit F1/F5/F6.)"""
    sessions, turns, tools = load_fixture()
    shipped = shipped_pack(sessions, turns, tools)
    opaque = re.compile(r"^sess_[0-9a-f]{10}$")
    items = shipped["candidate_sessions"]["items"]
    assert items, "fixture must produce candidate sessions"
    for it in items:
        assert opaque.match(it["source_ref"]), f"non-opaque source_ref: {it['source_ref']!r}"
        assert "base" not in it, "raw filename stem `base` must not ship in the shareable pack"
    blob = json.dumps(shipped)
    for s in sessions:
        b = s.get("base")
        if b:
            assert b not in blob, f"session base leaked into shareable pack: {b!r}"


def test_order_independent():
    """The shipped pack with reversed sessions AND reversed turns is identical (anonymization is a
    pure function of the pack, so it preserves build_pack's order-independence)."""
    sessions, turns, tools = load_fixture()
    a = shipped_pack(sessions, turns, tools)
    b = shipped_pack(list(reversed(sessions)), list(reversed(turns)), tools)
    assert canon(a) == canon(b), "pack changed under input reorder (selection/aggregation unstable)"


def test_insufficient_data_false_for_populated_corpus():
    sessions, turns, tools = load_fixture()
    pack = S.build_pack(sessions, iter(turns), tools)
    assert pack["corpus"]["insufficient_data"] is False
    assert pack["corpus"]["n_sessions"] >= S.MIN_CORPUS


# --------------------------------------------------------------------------- #
# --update: regenerate the fixtures + golden from the in-file corpus (synthetic only).
# --------------------------------------------------------------------------- #
if __name__ == "__main__" and "--update" in sys.argv:
    _sessions, _turns, _tools = build_corpus()
    write_fixture(_sessions, _turns, _tools)
    _pack = shipped_pack(_sessions, _turns, _tools)   # golden = the SHIPPED (anonymized) artifact
    with open(GOLDEN_PATH, "w") as _fh:
        json.dump(_pack, _fh, indent=2, sort_keys=True)
        _fh.write("\n")
    print(f"[update] wrote fixture -> {FIXTURE_DIR}")
    print(f"[update] wrote golden  -> {GOLDEN_PATH}")
    print(f"[update] {len(_sessions)} sessions / {len(_turns)} turns / "
          f"n_sessions={_pack['corpus']['n_sessions']} insufficient_data={_pack['corpus']['insufficient_data']}")
