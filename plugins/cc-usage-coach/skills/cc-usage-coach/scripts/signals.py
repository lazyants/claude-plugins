#!/usr/bin/env python3
"""
signals.py — turn the extracted dataset into a compact, deterministic SIGNAL PACK
for the cc-usage-coach skill. Python MEASURES (metrics + per-user baselines + ranked
candidates); the LLM at skill runtime CONCLUDES (significance, behavior, advice).

Emits:
  signal_pack.json   shareable, PATH-FREE, NO verdicts, NO magnitude thresholds.
  source_index.json  LOCAL ONLY (0600, gitignored): {source_ref -> absolute path}.

Design rules (see SKILL_PLAN.md): no hardcoded magnitude thresholds (only per-user
p50/p90 + mechanism-level constants below); no advice strings; reads are free for limits.
Run: python3 tools/signals.py
"""
import bisect, datetime, hashlib, json, os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_sessions as L   # shared out_dir() resolver (Change 4b)

# DATASET / output dir are resolved lazily in main() via L.out_dir() so importing this
# module (e.g. from tests) has no filesystem side effects.

# Mechanism-level constants (statistical validity / display caps) — NOT behavioral thresholds.
MIN_SLICE = 20        # a slice with n < MIN_SLICE is confidence:"low" (sample-size gate only)
MIN_CORPUS = 30       # < this many sessions -> insufficient_data
TOP_PROJECTS = 15
COST_BUCKET = 10
ANOMALY_BUCKET = 10
PER_SIGNAL = 3
CAND_CAP = 25
TRAILING_WINDOW = 3   # versions for the trailing-median comparison

# session_length turn-count buckets (display, NOT a behavioral threshold). Inclusive ranges;
# hi=None means open-ended. Labels are the pack keys for session_length.real_turns.histogram.
SESSION_LENGTH_BUCKETS = [
    (1, 1, "1"), (2, 5, "2-5"), (6, 10, "6-10"), (11, 20, "11-20"), (21, 50, "21-50"),
    (51, 100, "51-100"), (101, 300, "101-300"), (301, 1000, "301-1000"), (1001, None, "1000+"),
]

CURRENCY_NOTES = {
    "reads_excluded_from_limits": True,
    "read_exclusion_exceptions": ["claude-haiku-3.5"],
    "haiku_4_5_reads_uncertain": True,
    "output_limit_weight": "undocumented",
    "5m_write_means_overage": False,
}

# Per-session anomaly metrics (percentile-ranked within the user's own data).
ANOMALY_METRICS = ["cr_per_turn", "recache_excess_proxy", "n_turns", "peak_ctx"]


# --------------------------------------------------------------------------- pure helpers
def pct_rank(sorted_vals, v):
    """Fraction of values <= v (0..1). sorted_vals must be sorted ascending."""
    if not sorted_vals:
        return 0.0
    return bisect.bisect_right(sorted_vals, v) / len(sorted_vals)


def p50_p90(vals):
    xs = sorted(x for x in vals if x is not None)
    if not xs:
        return {"p50": None, "p90": None, "n": 0}
    return {"p50": _quantile(xs, 0.5), "p90": _quantile(xs, 0.9), "n": len(xs)}


def _quantile(sorted_xs, q):
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    pos = q * (len(sorted_xs) - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 < len(sorted_xs):
        return sorted_xs[lo] + frac * (sorted_xs[lo + 1] - sorted_xs[lo])
    return sorted_xs[lo]


def _bucket_label(n):
    for lo, hi, label in SESSION_LENGTH_BUCKETS:
        if n >= lo and (hi is None or n <= hi):
            return label
    return SESSION_LENGTH_BUCKETS[0][2]   # below smallest bucket (emitted sessions have n_turns>=1)


def _turn_histogram(vals):
    h = {label: 0 for _, _, label in SESSION_LENGTH_BUCKETS}
    for v in vals:
        h[_bucket_label(v)] += 1
    return h


def _turn_dist(vals):
    """p50/p90/p99/max/mean over a list of turn counts; all-None on empty (never quantile [])."""
    xs = sorted(v for v in vals if v is not None)
    if not xs:
        return {"p50": None, "p90": None, "p99": None, "max": None, "mean": None}
    return {"p50": round(_quantile(xs, 0.5), 1), "p90": round(_quantile(xs, 0.9), 1),
            "p99": round(_quantile(xs, 0.99), 1), "max": xs[-1],
            "mean": round(sum(xs) / len(xs), 1)}


def _dur_dist(vals):
    xs = sorted(v for v in vals if v is not None)
    if not xs:
        return {"p50": None, "p90": None, "max": None}
    return {"p50": round(_quantile(xs, 0.5), 1), "p90": round(_quantile(xs, 0.9), 1), "max": xs[-1]}


def session_length_block(sessions):
    """Path-free, verdict-free distribution of session length & shape. Order-independent.

    by_dir_class counts (sum to n_sessions) are a DIRECTORY classification, NOT a
    real-vs-side distinction: `real` = top-level sessions; `subagents`/`workflow` =
    separate side-thread JSONL files. real_with_side_turns = top-level sessions that
    fanned out inline. real_turns / real_dur_min describe real sessions only.
    """
    real = [s for s in sessions if s.get("d") == "real"]
    real_turns = [s.get("n_turns") or 0 for s in real]   # robust to missing/None; one entry per real session
    real_durs = [s.get("dur_min") for s in real if s.get("dur_min") is not None]
    return {
        "by_dir_class": {
            "real": len(real),
            "subagents": sum(1 for s in sessions if s.get("d") == "subagents"),
            "workflow": sum(1 for s in sessions if s.get("d") == "workflow"),
        },
        "real_with_side_turns": sum(1 for s in real if s.get("n_side", 0) > 0),
        "real_turns": {**_turn_dist(real_turns), "histogram": _turn_histogram(real_turns)},
        "real_dur_min": {**_dur_dist(real_durs),
                         "note": "wall-clock overstates active work; sessions resume across days"},
    }


def session_cr_per_turn(s):
    return s["cr"] / s["n_turns"] if s["n_turns"] else 0.0


def session_recache_excess(s):
    return max(0, s["cr"] - s.get("build_floor", 0))


def session_read_chars_per_call(s):
    return s["read_chars"] / s["n_read"] if s.get("n_read") else 0.0


def session_metric(s, key):
    if key == "cr_per_turn":
        return session_cr_per_turn(s)
    if key == "recache_excess_proxy":
        return session_recache_excess(s)
    if key == "read_chars_per_call":
        return session_read_chars_per_call(s)
    return s.get(key, 0)


def pareto_projects(proj_quota, total_quota):
    items = sorted(proj_quota.items(), key=lambda kv: (-kv[1], kv[0]))
    top = [{"p": p, "quota": q, "pct": round(100 * q / total_quota, 2)} for p, q in items[:TOP_PROJECTS]]
    cum = 0
    for t in top:
        cum += t["quota"]
        t["cum_pct"] = round(100 * cum / total_quota, 2)
    to81 = to90 = None
    c = 0
    for i, (p, q) in enumerate(items, 1):
        c += q
        if to81 is None and c / total_quota >= 0.81:
            to81 = i
        if to90 is None and c / total_quota >= 0.90:
            to90 = i
            break
    return top, to81, to90


def session_quota_shares(quotas, total):
    xs = sorted(quotas, reverse=True)
    n = len(xs)

    def top_share(frac):
        k = max(1, int(round(n * frac)))
        return round(100 * sum(xs[:k]) / total, 1) if total else 0.0
    return {"top1pct": top_share(0.01), "top5pct": top_share(0.05), "top10pct": top_share(0.10)}


def trailing_median_ratio(model_versions, idx):
    """model_versions: list of {v, cr_per_turn, n} ordered by (first_seen, version_string).
    Returns ratio of versions[idx].cr_per_turn to the median of the up-to-TRAILING_WINDOW
    immediately-preceding eligible (n>=MIN_SLICE) versions; None if no eligible prior."""
    prior = [mv["cr_per_turn"] for mv in model_versions[:idx] if mv["n"] >= MIN_SLICE][-TRAILING_WINDOW:]
    if not prior:
        return None
    med = statistics.median(prior)   # even window -> mean of two middle values
    if med == 0:
        return None
    return round(model_versions[idx]["cr_per_turn"] / med, 3)


# --------------------------------------------------------------------------- IO + assembly
def load_sessions(dataset):
    with open(os.path.join(dataset, "sessions.jsonl")) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def stream_turns(dataset):
    with open(os.path.join(dataset, "turns.jsonl")) as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def build_pack(sessions, turns_iter, tools):
    n_sessions = len(sessions)
    total = {"in": 0, "cr": 0, "rd": 0, "out": 0}
    proj_quota = {}
    # version slices: (version, model, main) ; model_task / behavior color
    vm = {}                       # (v, model) -> {n_turns, cr, rd, first_ts}
    by_model = {}                 # model -> {out:[...], tool_turns, n}
    no_tool_turns = no_tool_cr = total_turns = 0
    hour_hist = [0] * 24
    weekday_hist = [0] * 7        # isoweekday 1..7 -> index 0..6
    _wd_cache = {}
    for t in turns_iter:
        total["in"] += t["in"]; total["cr"] += t["cr"]; total["rd"] += t["rd"]; total["out"] += t["out"]
        q = t["in"] + t["cr"] + t["out"]
        proj_quota[t["p"]] = proj_quota.get(t["p"], 0) + q
        total_turns += 1
        if t.get("nt", 0) == 0:
            no_tool_turns += 1
            no_tool_cr += t["cr"]
        if t.get("t") == "main":
            key = (t["v"], t["m"])
            d = vm.setdefault(key, {"n_turns": 0, "cr": 0, "rd": 0, "first_ts": t["ts"]})
            d["n_turns"] += 1; d["cr"] += t["cr"]; d["rd"] += t["rd"]
            if t["ts"] and (d["first_ts"] is None or t["ts"] < d["first_ts"]):
                d["first_ts"] = t["ts"]
        bm = by_model.setdefault(t["m"], {"out": [], "tool_turns": 0, "n": 0})
        bm["out"].append(t["out"]); bm["n"] += 1
        if t.get("nt", 0) > 0:
            bm["tool_turns"] += 1
        ts = t.get("ts") or ""
        if len(ts) >= 13 and ts[11:13].isdigit():
            hour_hist[int(ts[11:13])] += 1
        if len(ts) >= 10:
            wd = _wd_cache.get(ts[:10])
            if wd is None:
                try:
                    wd = datetime.date.fromisoformat(ts[:10]).isoweekday()
                except ValueError:
                    wd = 0
                _wd_cache[ts[:10]] = wd
            if wd:
                weekday_hist[wd - 1] += 1
    total_quota = total["in"] + total["cr"] + total["out"]
    total_cr = total["cr"] or 1

    insufficient = n_sessions < MIN_CORPUS

    # ---- corpus
    total_recache_excess = sum(session_recache_excess(s) for s in sessions)
    total_first_cr = sum(s.get("first_cr") or 0 for s in sessions)
    dates = sorted(s["date"] for s in sessions if s.get("date"))
    corpus = {
        "quota": total_quota, "creation": total["cr"], "output": total["out"],
        "input": total["in"], "read": total["rd"],
        "n_sessions": n_sessions, "n_turns": total_turns, "n_projects": len(proj_quota),
        "date_range": [dates[0], dates[-1]] if dates else [None, None],
        "recache_share": round(total_recache_excess / total_cr, 4),
        "prefix_share": round(total_first_cr / total_cr, 4),
        "insufficient_data": insufficient,
    }
    split = {
        "creation_pct": round(100 * total["cr"] / (total_quota or 1), 2),
        "output_pct": round(100 * total["out"] / (total_quota or 1), 2),
        "input_pct": round(100 * total["in"] / (total_quota or 1), 2),
    }

    # ---- pareto
    top_projects, to81, to90 = pareto_projects(proj_quota, total_quota or 1)
    pareto = {"top_projects": top_projects, "projects_to_81pct": to81, "projects_to_90pct": to90,
              "session_quota_shares": session_quota_shares([s["quota"] for s in sessions], total_quota or 1)}

    # ---- baselines (per-user)
    baselines = {
        "turns": p50_p90([s["n_turns"] for s in sessions]),
        "peak_ctx": p50_p90([s["peak_ctx"] for s in sessions]),
        "cr_per_turn": p50_p90([session_cr_per_turn(s) for s in sessions]),
        "recache_excess_proxy": p50_p90([session_recache_excess(s) for s in sessions]),
        "read_chars_per_call": p50_p90([session_read_chars_per_call(s) for s in sessions if s.get("n_read")]),
    }

    # ---- candidate_sessions (multi-bucket, deterministic; NO score verdict)
    cand = select_candidates(sessions, total_quota or 1, baselines)

    # ---- fan_out
    side = [s for s in sessions if s["d"] in ("workflow", "subagents") or s["n_side"] > 0]
    sub = [s for s in sessions if s["d"] == "subagents"]
    wf = [s for s in sessions if s["d"] == "workflow"]
    side_cr = sum(s["cr"] for s in side)
    trivial_sub = sum(1 for s in sub if s["n_turns"] <= 2)
    fan_out = {
        "side_cr_share": round(side_cr / total_cr, 4),
        "subagent_share": round(sum(s["quota"] for s in sub) / (total_quota or 1), 4),
        "workflow_share": round(sum(s["quota"] for s in wf) / (total_quota or 1), 4),
        "trivial_subagent_rate": round(trivial_sub / len(sub), 4) if sub else 0.0,
        "spinup_first_cr_median": int(statistics.median([s["first_cr"] for s in sub if s.get("first_cr")]))
                                  if any(s.get("first_cr") for s in sub) else None,
        "n": len(side),
    }

    # ---- version_signals_by_model (model-held, NOT flagged)
    version_signals = version_signals_by_model(vm)

    # ---- tool_injection
    trb = tools.get("tool_result_bytes", {})
    total_inj = sum(v["est_tokens"] for v in trb.values()) or 1
    by_tool = [{"tool": k, "est_tokens": v["est_tokens"],
                "pct": round(100 * v["est_tokens"] / total_inj, 1),
                "chars_per_call": round(v["chars"] / v["count"]) if v["count"] else 0}
               for k, v in list(trb.items())[:12]]
    tool_injection = {"by_tool": by_tool,
                      "injection_to_creation_mult": round(total["cr"] / total_inj, 1),
                      "note": "injection_to_creation_mult is an aggregate ratio, not a per-chunk re-cache count"}

    # ---- behavior_color (non-core) + model_task_fit_signals (facts only)
    behavior_color = {
        "no_tool_turn_share": round(no_tool_turns / (total_turns or 1), 4),
        "no_tool_cr_share": round(no_tool_cr / total_cr, 4),
        "hour_histogram_utc": hour_hist,
        "weekday_split": weekday_hist,          # isoweekday Mon..Sun -> index 0..6
        "note": "color, not a lever",
    }
    model_task_fit = {m: {"median_out": int(statistics.median(d["out"])) if d["out"] else 0,
                          "tool_turn_share": round(d["tool_turns"] / d["n"], 3) if d["n"] else 0.0,
                          "n": d["n"]}
                      for m, d in by_model.items()}

    session_length = session_length_block(sessions)

    return {
        "schema_version": 3,
        "corpus": corpus, "split": split, "pareto": pareto, "baselines": baselines,
        "session_length": session_length,
        "candidate_sessions": cand, "fan_out": fan_out,
        "version_signals_by_model": version_signals,
        "tool_injection": tool_injection,
        "behavior_color": behavior_color,
        "model_task_fit_signals": model_task_fit,
        "currency_notes": CURRENCY_NOTES,
    }


def version_signals_by_model(vm):
    """Per (version, model, main) cr/turn + trailing-median ratio. Model-held, NOT flagged."""
    by_model = {}
    for (v, model), d in vm.items():
        by_model.setdefault(model, []).append(
            {"v": v, "model": model, "n_turns": d["n_turns"], "first_ts": d["first_ts"],
             "cr_per_turn": round(d["cr"] / d["n_turns"], 1) if d["n_turns"] else 0.0,
             # reuse_ratio = reads per creation token (warm-reuse efficiency); the metric
             # ANALYSIS.md used for the v2.1.158 churn finding. Higher = better reuse.
             "reuse_ratio": round(d["rd"] / d["cr"], 1) if d["cr"] else None})
    out = []
    # sort model groups so emission order is deterministic regardless of turn-stream order
    # (within-model order stays (first_ts, v)); fixes a reversed-turns order-dependence.
    for model, lst in sorted(by_model.items()):
        lst.sort(key=lambda x: (x["first_ts"] or "", x["v"]))   # tiebreak equal ts by version string
        ordered = [{"cr_per_turn": x["cr_per_turn"], "n": x["n_turns"]} for x in lst]
        for i, mv in enumerate(lst):
            n = mv["n_turns"]
            out.append({"v": mv["v"], "model": model, "n_turns": n,
                        "cr_per_turn": mv["cr_per_turn"], "reuse_ratio": mv["reuse_ratio"],
                        "ratio_vs_trailing_median": trailing_median_ratio(ordered, i),
                        "n": n, "confidence": "low" if n < MIN_SLICE else "ok"})
    return out


def _read_token(leaf_hash):
    """Opaque file token for the SHAREABLE pack's repeat_reads. The dataset entry is
    `leaf#<one-way-hash>`; keep ONLY the trailing hash (`-> file_<hash>`) and drop the real
    filename — a filename can embed a project / client / sensitive name (verified on real data:
    project names appeared inside re-read filenames), so it must not reach the shared pack. The
    repeat COUNT (the actual churn signal) is preserved; the real filename stays in the LOCAL-ONLY
    dataset. PR-review follow-up to project anonymization.

    Hardened (no pass-through): only the trailing segment after the last `#` is kept, and only when
    it is a non-empty hex hash. Any other shape — no `#` (`orphan`), a non-hex tail, or a non-string
    entry — is hashed whole rather than emitted verbatim, so a malformed/corrupt dataset entry can
    never leak a filename. Mirrors `_proj_id`'s hash-everything rule."""
    s = str(leaf_hash)
    _, sep, tail = s.rpartition("#")
    if sep and tail and all(c in "0123456789abcdef" for c in tail):
        return "file_" + tail
    return "file_" + hashlib.sha1(s.encode("utf-8", errors="replace")).hexdigest()[:6]


def select_candidates(sessions, total_quota, baselines):
    """Deterministic multi-bucket candidate selection. Emits ranks/factors, NO verdict."""
    # precompute per-session metric values + within-user percentile ranks
    metric_sorted = {m: sorted(session_metric(s, m) for s in sessions) for m in ANOMALY_METRICS}
    enriched = []
    for s in sessions:
        cost_pct = round(100 * s["quota"] / total_quota, 3)
        factors = {m: round(pct_rank(metric_sorted[m], session_metric(s, m)), 3) for m in ANOMALY_METRICS}
        anomaly = max(factors.values()) if factors else 0.0
        why = []
        for m in ANOMALY_METRICS:
            bl = baselines.get(m)
            if bl and bl["p90"] is not None and session_metric(s, m) > bl["p90"]:
                why.append(m)
        if s.get("n_comp", 0) > 0:
            why.append("compaction")
        if s.get("n_repeat_read_paths", 0) > 0:
            why.append("repeat_read")
        enriched.append({"s": s, "cost_pct": cost_pct, "quota": s["quota"],
                         "anomaly": anomaly, "factors": factors, "why": why})

    cost_sorted = sorted(enriched, key=lambda e: (-e["cost_pct"], -e["quota"], e["s"]["s"]))
    anom_sorted = sorted(enriched, key=lambda e: (-e["anomaly"], e["s"]["s"]))
    chosen, buckets = {}, {}

    def add(e, bucket):
        ref = e["s"]["s"]
        chosen.setdefault(ref, e)
        buckets.setdefault(ref, []).append(bucket)

    for e in cost_sorted[:COST_BUCKET]:
        add(e, "cost")
    for e in anom_sorted[:ANOMALY_BUCKET]:
        add(e, "anomaly")
    for m in ANOMALY_METRICS:
        per = sorted(enriched, key=lambda e: (-e["factors"][m], e["s"]["s"]))[:PER_SIGNAL]
        for e in per:
            add(e, f"signal:{m}")

    pre_dedup = COST_BUCKET + ANOMALY_BUCKET + PER_SIGNAL * len(ANOMALY_METRICS)
    post_dedup = len(chosen)
    # rank maps for emitting cost_rank / anomaly_rank
    cost_rank = {e["s"]["s"]: i + 1 for i, e in enumerate(cost_sorted)}
    anom_rank = {e["s"]["s"]: i + 1 for i, e in enumerate(anom_sorted)}

    ordered = sorted(chosen.values(), key=lambda e: (-e["cost_pct"], -e["quota"], e["s"]["s"]))[:CAND_CAP]
    out = []
    for e in ordered:
        s = e["s"]
        out.append({
            "source_ref": s["s"], "base": s["base"], "p": s["p"],
            "n_turns": s["n_turns"], "peak_ctx": s["peak_ctx"], "cr": s["cr"],
            "build_floor": s.get("build_floor", 0), "n_epochs": s.get("n_epochs", 1),
            "n_comp": s.get("n_comp", 0), "n_models": s.get("n_models", 1),
            "recache_excess_proxy": session_recache_excess(s),
            "recache_excess_note": "ROUGH directional proxy, not a bound (can over/understate)",
            "cr_peak_mult": round(s["cr"] / s["peak_ctx"], 2) if s["peak_ctx"] else None,
            "cost_pct": e["cost_pct"], "cost_rank": cost_rank[s["s"]],
            "anomaly_rank": anom_rank[s["s"]], "anomaly_factors": e["factors"],
            "selection_buckets": sorted(set(buckets[s["s"]])), "why": e["why"],
            "read_evidence": {"n_read": s.get("n_read", 0), "read_chars": s.get("read_chars", 0),
                              # opaque file token only — the real filename (which can embed a
                              # project/client name) stays in the LOCAL-ONLY dataset, not the pack.
                              "repeat_reads": [[_read_token(rr[0]), rr[1]] for rr in s.get("repeat_reads", [])]},
        })
    return {
        "items": out,
        "omitted_candidate_counts": {"pre_dedup": pre_dedup, "post_dedup": post_dedup,
                                     "post_cap": max(0, post_dedup - len(out))},
    }


# -- project anonymization: the shareable pack carries OPAQUE project IDs; the id->name map is
#    LOCAL-ONLY (project_index.json), resolved by the report (mirrors source_ref/source_index).
def _proj_id(name):
    """Opaque, stable, name-free ID for a project label in the SHAREABLE pack (one-way sha1). EVERY
    label is hashed — nothing passes through unmasked — so a real project that happens to be named
    like a fallback ("unknown"/"?") cannot leak, and a non-string is coerced (never crashes). The
    report resolves IDs back to names via the LOCAL-ONLY project_index.json. codex review."""
    return "proj_" + hashlib.sha1(str(name).encode("utf-8")).hexdigest()[:10]


def anonymize_projects(pack):
    """Return (anonymized_pack, {opaque_id: real_name}). Every project label in the shareable pack
    (`pareto.top_projects[].p` and `candidate_sessions.items[].p` — the only fields that carry one)
    becomes an opaque _proj_id; the id->name map is LOCAL-ONLY (project_index.json). A shared pack
    thus discloses no project/client/repo name; the user's report resolves names from the map,
    exactly as source_ref resolves via source_index. Pure + deterministic, so it preserves the
    build_pack(reversed)==build_pack invariant."""
    mapping = {}

    def oid(name):
        pid = _proj_id(name)            # every label is hashed -> always recorded for resolution
        mapping[pid] = name
        return pid

    anon = dict(pack)
    pareto = dict(pack["pareto"])
    pareto["top_projects"] = [{**tp, "p": oid(tp["p"])} for tp in pack["pareto"]["top_projects"]]
    anon["pareto"] = pareto
    cs = dict(pack["candidate_sessions"])
    cs["items"] = [{**it, "p": oid(it["p"])} for it in pack["candidate_sessions"]["items"]]
    anon["candidate_sessions"] = cs
    return anon, mapping


def _write_local_json(path, obj):
    """Write a LOCAL-ONLY JSON file at 0600 with no umask window, refusing a planted symlink
    (O_NOFOLLOW). Returns True on success; on failure prints a PATH-FREE error to stderr and
    returns False — NEVER prints `path` (an absolute path on stderr is the leak we guard against)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError:
        print("error: could not write local index", file=sys.stderr)
        return False
    try:
        os.fchmod(fd, 0o600)            # enforce 0600 even if it pre-existed looser — FATAL if it fails
    except OSError:
        os.close(fd)                    # fd still raw (not fdopen'd) -> close it, don't half-write
        print("error: could not secure local index", file=sys.stderr)
        return False
    with os.fdopen(fd, "w") as fh:      # fdopen now owns fd and closes it on exit
        json.dump(obj, fh, sort_keys=True)
    return True


def main():
    out = L.out_dir()
    dataset = os.path.join(out, "dataset")
    sessions = load_sessions(dataset)
    tools = json.load(open(os.path.join(dataset, "tools.json")))
    pack = build_pack(sessions, stream_turns(dataset), tools)
    pack, proj_index = anonymize_projects(pack)   # shareable pack carries OPAQUE project IDs only
    pack_path = os.path.join(out, "signal_pack.json")
    with open(pack_path, "w") as fh:
        json.dump(pack, fh, indent=2)
    # LOCAL-ONLY indexes (0600, gitignored, never shared): source_ref -> ABSOLUTE PATH, and
    # opaque project id -> REAL project name (so the report can show real names).
    index = {s["s"]: s["source_path"] for s in sessions if s.get("source_path")}
    if not _write_local_json(os.path.join(out, "source_index.json"), index):
        return 1
    if not _write_local_json(os.path.join(out, "project_index.json"), proj_index):
        return 1
    c = pack["corpus"]
    # Print NO absolute path to stdout — this stream enters the skill LLM's context.
    print(f"signal_pack.json written ({os.path.getsize(pack_path)//1024} KB)")
    print(f"  sessions={c['n_sessions']:,} quota={c['quota']:,} recache_share={c['recache_share']} "
          f"candidates={len(pack['candidate_sessions']['items'])}")
    print(f"source_index.json + project_index.json written "
          f"({len(index)} refs, {len(proj_index)} projects; LOCAL, 0600)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
