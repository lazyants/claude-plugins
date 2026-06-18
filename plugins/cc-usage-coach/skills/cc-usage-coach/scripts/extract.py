#!/usr/bin/env python3
"""
extract.py — ONE streaming pass over all CC session logs, emitting a rich per-turn and
per-session dataset for downstream token/limit-saving analysis. Reuses the verified
lib_sessions primitives + the measure.py dedup discipline (composite key AFTER validity).

Outputs (under ../dataset/):
  turns.jsonl     one record per assistant-with-usage turn (deduped, matches measure.py totals)
  sessions.jsonl  per-session rollups
  tools.json      tool-use frequency + tool_result byte/est-token cost by tool (approx)
  meta.json       corpus totals for cross-validation vs results.json

Run: python3 tools/extract.py
"""
import json, os, sys, hashlib, ntpath
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_sessions as L

# OUT_DIR is resolved lazily in main() via L.out_dir() (Change 4b) so importing this
# module has no filesystem side effects (plugin install dir may be read-only).


def auf(entry):
    """usage-fields for a non-error assistant-with-usage entry; else None (mirrors measure.py)."""
    msg = entry.get("message") or {}
    if msg.get("role") != "assistant":
        return None
    u = msg.get("usage")
    if not u:
        return None
    if L.is_error_or_empty(entry):
        return None
    uf = L.usage_fields(u)
    if not any(uf.values()):
        return None
    return uf


def _self_identity_tokens():
    """Lower-cased local-user identity tokens (login name + home-dir leaf). The tool only ever reads
    the CURRENT user's own session logs, so a path leaf equal to one of these IS this user's
    username — drop it. This is the backstop that closes a relocated home under a non-standard parent
    (e.g. /Volumes/Data/<user>, D:\\Profiles\\<user>), which a pure path-SHAPE rule cannot see; here
    we legitimately DO know $HOME (mirrors arc.py's literal-home redaction). PR #1 review."""
    home = os.path.expanduser("~")
    toks = {ntpath.basename(home.rstrip("/\\")), os.environ.get("USER", ""), os.environ.get("LOGNAME", "")}
    return {t.lower() for t in toks if t}


_SELF = _self_identity_tokens()


def _home_root(d):
    """True if dir `d` is a multi-user home root (its immediate child is therefore a username): its
    own trailing component is 'Users' (macOS/Windows) or 'home' (Linux). Splitting on BOTH
    separators normalizes every separator/drive/mount/UNC form at once — /Users, C:\\Users,
    C:/Users, /mnt/c/Users, /Volumes/x/Users, \\\\server\\Users (ntpath.basename returns "" for a
    UNC share root, so we split manually). A non-home dir literally named 'Users'/'home' only costs
    a rarely-hit 'unknown' label, never a leak — the safe way to err."""
    leaf = d.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
    return leaf in ("users", "home")


def _safe_leaf(p):
    """Path-free leaf (basename) of `p` for the SHAREABLE pack, or None if exposing it could leak a
    username or an un-split absolute path. Parsed with ntpath — a superset understanding POSIX '/',
    Windows '\\' and drive letters — so a path recorded on another OS is split correctly: a username
    PARENT (.../Users/<user>, .../home/<user>, incl. UNC and mount forms) is dropped and only the
    bare filename/project leaf is kept. Also dropped: the home-root dir itself ('users'/'home'/
    'root'), a ':'-bearing leaf (un-split drive / alternate-data-stream), a mangled CC dir
    ('-Users-…'), and any leaf equal to the LOCAL user's login/home name (`_SELF`) — the backstop
    that closes a *relocated* bare home under a non-standard parent ('/Volumes/Data/<user>',
    'D:\\Profiles\\<user>'), which path SHAPE alone cannot detect.
    codex/security/PR-review: posixpath alone leaked 'C:/Users/<user>', 'C:\\Users\\<user>' and UNC;
    a non-standard relocated home leaked the bare username until the `_SELF` check was added."""
    p = str(p).rstrip("/\\")
    if _home_root(ntpath.dirname(p)):                    # parent is a home root -> leaf is a username
        return None
    base = ntpath.basename(p)
    if (not base or ":" in base
            or base.lower() in ("users", "home", "root")        # the home-root dir itself
            or base.lower() in _SELF                            # the local user's own login/home name
            or base.lower().startswith(("-users-", "-home-"))):  # mangled full cwd
        return None
    return base


def proj_of(entry, path):
    """Human project label for the (shareable) pack — PATH-FREE (no abs path / username). The cwd
    basename is the project leaf; a cwd that IS a home dir, a mangled full path, or a missing cwd
    -> "unknown" rather than leaking. codex r2 HIGH; Windows drive forms hardened (review)."""
    cwd = entry.get("cwd")
    return (_safe_leaf(cwd) if cwd else None) or "unknown"


def dir_class(path):
    p = L.project_of(path)
    if p == "subagents":
        return "subagents"
    if p.startswith("wf_"):
        return "workflow"
    return "real"


def blocks(content, btype):
    """Yield dict content-blocks of the given type (skips non-list / non-dict)."""
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict) and b.get("type") == btype:
            yield b


def tool_result_chars(block):
    """Approx serialized size of a tool_result block's content."""
    c = block.get("content")
    if isinstance(c, str):
        return len(c)
    try:
        return len(json.dumps(c))
    except Exception:
        return 0


def segmented_build_floor(floor_seq):
    """Build-floor = one build per (compaction-epoch x model). SKILL_PLAN R3/R4.
    floor_seq: ordered list of {"boundary":bool, "model":str, "ctx":int} for emitted turns;
    boundary=True means a compaction marker fired since the previous emitted turn (opens a new epoch).
    Returns (build_floor, n_epochs, n_model_epoch_groups)."""
    epoch = 0
    peak = {}
    for t in floor_seq:
        if t["boundary"]:
            epoch += 1
        k = (epoch, t["model"])
        peak[k] = max(peak.get(k, 0), t["ctx"])   # always record the (epoch,model) group
    return sum(peak.values()), len({k[0] for k in peak}), len(peak)


def main():
    OUT_DIR = os.path.join(L.out_dir(), "dataset")
    os.makedirs(OUT_DIR, exist_ok=True)
    files = L.discover_files()
    seen_global = set()

    # corpus aggregates
    tot = {"turns": 0, "in": 0, "cr": 0, "rd": 0, "out": 0, "c5": 0, "c1": 0,
           "err": 0, "comp": 0, "sessions": 0}
    tool_use_freq = defaultdict(int)            # tool name -> # of tool_use blocks
    tool_result = defaultdict(lambda: [0, 0])   # tool name -> [result_chars, result_count]

    fturns = open(os.path.join(OUT_DIR, "turns.jsonl"), "w")
    fsess = open(os.path.join(OUT_DIR, "sessions.jsonl"), "w")

    for fi, path in enumerate(files):
        base = os.path.basename(path)[:-6] if path.endswith(".jsonl") else os.path.basename(path)
        # basename (session uuid / agent id) collides across project dirs -> append a realpath
        # hash for a unique join key (codex review LOW). Keep `base` for human readability.
        sid = f"{base}-{hashlib.sha1(path.encode()).hexdigest()[:6]}"
        dc = dir_class(path)
        entries = [o for o in L.iter_entries(path) if isinstance(o, dict)]   # skip non-dict JSON lines
        if not entries:
            continue
        seen_tr = set()        # intra-file tool_result dedup (codex review LOW)

        # tool_use_id -> tool name (for attributing tool_result sizes)
        tuid_name = {}
        for o in entries:
            for b in blocks((o.get("message") or {}).get("content"), "tool_use"):
                tuid_name[b.get("id")] = b.get("name")

        # per-session rollup accumulators
        srow = {"s": sid, "base": base, "d": dc, "models": set(), "vers": set(),
                "proj_q": defaultdict(int),
                "n_turns": 0, "n_side": 0, "in": 0, "cr": 0, "rd": 0, "out": 0,
                "peak_ctx": 0, "first_cr": None, "min_rd": None,
                "n_5m": 0, "n_1h": 0, "n_comp": 0, "n_err": 0,
                "n_read": 0, "read_chars": 0, "read_paths": defaultdict(int),
                "start": None, "end": None}
        # build-floor by compaction-epoch x model (one build per (epoch,model)) — SKILL_PLAN R3/R4
        floor_seq = []
        pending_boundary = False

        prev_entry = None
        for o in entries:
            if o.get("isMeta") or o.get("isSnapshotUpdate") or o.get("isVisibleInTranscriptOnly"):
                prev_entry = o
                continue
            msg = o.get("message") or {}

            # error turns (excluded from auf) -> count for the retry/error tax analysis
            if L.is_error_or_empty(o):
                srow["n_err"] += 1
                tot["err"] += 1
            # compaction markers — also close the prior build-floor epoch (next emitted turn opens new)
            if L.has_compaction_marker(o, prev_entry):
                srow["n_comp"] += 1
                tot["comp"] += 1
                pending_boundary = True

            # tool_result sizes (user turns) -> attribute to the tool by tool_use_id.
            # Skip intra-file duplicate tool_result blocks (codex review LOW) so re-logged
            # user turns don't inflate the injection totals.
            cont = msg.get("content")
            for b in blocks(cont, "tool_result"):
                trk = (o.get("uuid"), o.get("agentId"), o.get("parentUuid"), b.get("tool_use_id"))
                if trk in seen_tr:
                    continue
                seen_tr.add(trk)
                nm = tuid_name.get(b.get("tool_use_id"), "?")
                ch = tool_result_chars(b)
                tool_result[nm][0] += ch
                tool_result[nm][1] += 1
                if nm == "Read":
                    srow["read_chars"] += ch

            uf = auf(o)
            if uf is None:
                prev_entry = o
                continue

            # composite-key dedup AFTER validity (codex round-3) — keeps corpus totals == measure.py
            u = o.get("uuid")
            if u is not None:
                eid = (u, o.get("agentId"), o.get("parentUuid"))
                if eid in seen_global:
                    prev_entry = o
                    continue
                seen_global.add(eid)

            tp = proj_of(o, path)        # per-turn project (one file can span projects) — codex review HIGH
            model = (msg.get("model") or "?")
            ver = o.get("version", "?")
            ts = o.get("timestamp")
            side = bool(o.get("isSidechain"))
            wt = L.write_type(uf)
            ctx = uf["read"] + uf["creation"]

            ntools = 0
            for b in blocks(cont, "tool_use"):
                ntools += 1
                tool_use_freq[b.get("name")] += 1
                if b.get("name") == "Read":
                    srow["n_read"] += 1
                    fp = (b.get("input") or {}).get("file_path")
                    if fp:
                        srow["read_paths"][fp] += 1

            rec = {"s": sid, "p": tp, "d": dc, "v": ver, "m": model,
                   "t": "side" if side else "main", "ts": ts,
                   "in": uf["input"], "cr": uf["creation"], "rd": uf["read"], "out": uf["output"],
                   "c5": uf["c5"], "c1": uf["c1"], "wt": wt, "nt": ntools}
            fturns.write(json.dumps(rec, separators=(",", ":")) + "\n")

            # rollups
            srow["models"].add(model); srow["vers"].add(ver)
            srow["proj_q"][tp] += uf["input"] + uf["creation"] + uf["output"]   # per-project quota in this session
            srow["n_turns"] += 1
            if side:
                srow["n_side"] += 1
            srow["in"] += uf["input"]; srow["cr"] += uf["creation"]
            srow["rd"] += uf["read"]; srow["out"] += uf["output"]
            srow["peak_ctx"] = max(srow["peak_ctx"], ctx)
            floor_seq.append({"boundary": pending_boundary, "model": model, "ctx": ctx})
            pending_boundary = False
            if srow["first_cr"] is None and uf["creation"] > 0:
                srow["first_cr"] = uf["creation"]
            if uf["read"] > 0:
                srow["min_rd"] = uf["read"] if srow["min_rd"] is None else min(srow["min_rd"], uf["read"])
            if wt == "5m":
                srow["n_5m"] += 1
            elif wt == "1h":
                srow["n_1h"] += 1
            if srow["start"] is None or (ts and ts < srow["start"]):
                srow["start"] = ts
            if srow["end"] is None or (ts and ts > srow["end"]):
                srow["end"] = ts

            # corpus totals
            tot["turns"] += 1
            tot["in"] += uf["input"]; tot["cr"] += uf["creation"]
            tot["rd"] += uf["read"]; tot["out"] += uf["output"]
            tot["c5"] += uf["c5"]; tot["c1"] += uf["c1"]

            prev_entry = o

        if srow["n_turns"] == 0:
            continue
        ts0, ts1 = L.parse_iso(srow["start"]), L.parse_iso(srow["end"])
        dur_min = round((ts1 - ts0).total_seconds() / 60.0, 1) if (ts0 and ts1) else None
        # session's primary project = the one with the most quota in it (codex review HIGH);
        # n_proj flags multi-project sessions so downstream can split if needed.
        prim = max(srow["proj_q"].items(), key=lambda kv: kv[1])[0] if srow["proj_q"] else "?"
        build_floor, n_epochs, n_model_epoch_groups = segmented_build_floor(floor_seq)
        repeat_reads = sorted(
            ([leaf + "#" + hashlib.sha1(p.encode()).hexdigest()[:6], c]
             for p, c in srow["read_paths"].items()
             if c > 1 and (leaf := _safe_leaf(p))),   # leaf=None (leaky) -> entry dropped
            key=lambda x: (-x[1], x[0]))[:5]
        n_repeat_read_paths = sum(1 for c in srow["read_paths"].values() if c > 1)
        out = {"s": srow["s"], "base": srow["base"], "source_path": path,
               "p": prim, "n_proj": len(srow["proj_q"]), "d": srow["d"],
               "models": sorted(srow["models"]), "vers": sorted(srow["vers"]),
               "n_models": len(srow["models"]), "n_versions": len(srow["vers"]),
               "n_turns": srow["n_turns"], "n_side": srow["n_side"],
               "in": srow["in"], "cr": srow["cr"], "rd": srow["rd"], "out": srow["out"],
               "quota": srow["in"] + srow["cr"] + srow["out"],
               "peak_ctx": srow["peak_ctx"], "first_cr": srow["first_cr"], "min_rd": srow["min_rd"],
               "build_floor": build_floor, "n_epochs": n_epochs,
               "n_model_epoch_groups": n_model_epoch_groups,
               "n_read": srow["n_read"], "read_chars": srow["read_chars"],
               "repeat_reads": repeat_reads, "n_repeat_read_paths": n_repeat_read_paths,
               "n_5m": srow["n_5m"], "n_1h": srow["n_1h"],
               "has_5m_writes": srow["n_5m"] > 0, "n_comp": srow["n_comp"], "n_err": srow["n_err"],
               "start": srow["start"], "end": srow["end"], "dur_min": dur_min,
               "date": (srow["start"] or "")[:10]}
        fsess.write(json.dumps(out, separators=(",", ":")) + "\n")
        tot["sessions"] += 1

        if (fi + 1) % 1000 == 0:
            print(f"... {fi+1}/{len(files)} files", file=sys.stderr)

    fturns.close(); fsess.close()
    # sessions.jsonl carries full realpaths (source_path) -> local-only, 0600 (SKILL_PLAN R3-2)
    try:
        os.chmod(os.path.join(OUT_DIR, "sessions.jsonl"), 0o600)
    except OSError:
        pass

    tools_out = {
        "tool_use_freq": dict(sorted(tool_use_freq.items(), key=lambda x: -x[1])),
        "tool_result_bytes": {k: {"chars": v[0], "est_tokens": v[0] // 4, "count": v[1]}
                              for k, v in sorted(tool_result.items(), key=lambda x: -x[1][0])},
    }
    with open(os.path.join(OUT_DIR, "tools.json"), "w") as fh:
        json.dump(tools_out, fh, indent=2)
    tot["quota"] = tot["in"] + tot["cr"] + tot["out"]
    with open(os.path.join(OUT_DIR, "meta.json"), "w") as fh:
        json.dump({"files": len(files), "totals": tot,
                   "schema": "turns:{s,p,d,v,m,t,ts,in,cr,rd,out,c5,c1,wt,nt}; "
                             "sessions:{s,base,source_path,p,n_proj,d,models,vers,n_models,n_versions,"
                             "n_turns,n_side,in,cr,rd,out,quota,peak_ctx,first_cr,min_rd,build_floor,"
                             "n_epochs,n_model_epoch_groups,n_read,read_chars,repeat_reads,"
                             "n_repeat_read_paths,n_5m,n_1h,has_5m_writes,n_comp,n_err,start,end,dur_min,date}"},
                  fh, indent=2)

    print(f"turns={tot['turns']:,}  sessions={tot['sessions']:,}  quota(in+cr+out)={tot['quota']:,}")
    print(f"  in={tot['in']:,} cr={tot['cr']:,} rd={tot['rd']:,} out={tot['out']:,}")
    print(f"  err_turns={tot['err']:,} comp_markers={tot['comp']:,}")
    print("dataset written (set CC_COACH_OUT to relocate)")   # no absolute path to stdout


if __name__ == "__main__":
    main()
