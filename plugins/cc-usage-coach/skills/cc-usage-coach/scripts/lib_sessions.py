"""
lib_sessions.py — pure, testable core for measuring cold-cache waste from idle waits
in Claude Code session logs.  See ../PLAN.md (v5) for the methodology this implements.

Design rule: everything that decides a number is a PURE function taking plain
dict/number inputs (no filesystem), so it is unit-tested without real logs.
Parsing/IO helpers are separated at the bottom.
"""
from __future__ import annotations
import hashlib, json, os, glob, math, tempfile

# ---------------------------------------------------------------------------
# Constants (PLAN §2). Billing weights are relative to base input price.
# ---------------------------------------------------------------------------
READ_MULT      = 0.1      # cache read  = 0.1x base input
WRITE_MULT_5M  = 1.25     # 5-minute cache write
WRITE_MULT_1H  = 2.0      # 1-hour cache write
OUTPUT_MULT    = 5.0      # output ≈ 5x input (Opus)

TTL_SECONDS = {"1h": 3600, "5m": 300}

# Per-model BASE input $/MTok (Opus 4.8 CONFIRMED 2026-06-17; others APPROX, flagged).
# Quota (the currency that matters) is token-based and model-agnostic, so these only
# affect the $-reference figure. Override via measure.py if exact prices are confirmed.
MODEL_BASE_PRICE = {  # ($/MTok input, $/MTok output)
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),          # APPROX
    "claude-haiku-4-5-20251001": (1.0, 5.0),   # APPROX
    "claude-fable-5": (5.0, 25.0),             # APPROX
}
DEFAULT_BASE_PRICE = (5.0, 25.0)  # fall back to Opus pricing

# Thresholds (PLAN §3/§4.1)
GATE_TOLERANCE_FRAC   = 0.10   # wait-attributable if gap >= protecting_ttl*(1-this) (TTL is a *minimum* life)
HARD_SHRINK           = 0.60   # cached_bytes(i) < 0.60*cached_bytes(i-1) -> compaction (secondary)
AUDIT_SHRINK_LO       = 0.60   # 0.60..0.85 -> audit (ambiguous) bucket
AUDIT_SHRINK_HI       = 0.85
STRUCT_GROWTH         = 1.50   # cold + cached_bytes(i) > 1.50*cached_bytes(i-1) -> ambiguous (struct growth)
COLD_READ_FRAC        = 0.10   # cache_read(i) < this * cached_bytes(i-1) => "read≈0"
WARM_READ_FRAC        = 0.90   # cache_read(i) >= this * cached_bytes(i-1) => WARM

GAP_BUCKETS_MIN = [(0,2),(2,5),(5,10),(10,15),(15,30),(30,60),(60,120),(120,float("inf"))]


# ---------------------------------------------------------------------------
# Usage extraction (PLAN §1/§3)
# ---------------------------------------------------------------------------
def usage_fields(usage: dict) -> dict:
    """Normalize a message.usage dict into the numbers we need."""
    u = usage or {}
    cc = u.get("cache_creation") or {}
    c5 = cc.get("ephemeral_5m_input_tokens", 0) or 0
    c1 = cc.get("ephemeral_1h_input_tokens", 0) or 0
    # prefer the per-tier breakdown sum when present; else fall back to the flat field
    breakdown_total = c5 + c1
    creation_total = breakdown_total if breakdown_total > 0 else (u.get("cache_creation_input_tokens", 0) or 0)
    return {
        "input": u.get("input_tokens", 0) or 0,
        "output": u.get("output_tokens", 0) or 0,
        "read": u.get("cache_read_input_tokens", 0) or 0,
        "creation": creation_total,
        "c5": c5, "c1": c1,
    }


def cached_bytes(uf: dict) -> int:
    """Cache state established AFTER this turn (excludes the uncached tail input_tokens). PLAN §3 / codex r1 F1."""
    return uf["read"] + uf["creation"]


def write_type(uf: dict):
    """'1h' | '5m' | None (None when the turn wrote nothing — read-only)."""
    if uf["creation"] <= 0:
        return None
    if uf["c1"] > 0 and uf["c5"] == 0:
        return "1h"
    if uf["c5"] > 0 and uf["c1"] == 0:
        return "5m"
    if uf["c1"] > 0 and uf["c5"] > 0:
        return "both"      # plan assumes absent; flag if seen
    return None            # creation>0 but breakdown missing -> unknown tier


# ---------------------------------------------------------------------------
# Core waste metric (PLAN §3, codex r1 F1 + r2 F1 cap)
# ---------------------------------------------------------------------------
def waste_tokens(prev_cached: int, cur_cached: int, read_i: int) -> int:
    """
    Prior CACHED context turn i had to re-write instead of reading warm.
    min(prev,cur) caps the shrink case (pruned tokens were dropped, not rebuilt):
      grow   -> prev_cached - read_i
      shrink -> cur_cached  - read_i  == cache_creation(i)
    """
    return max(0, min(prev_cached, cur_cached) - read_i)


def rewrite_mult(wt_i) -> float:
    """$ multiplier turn i actually paid to re-write the wasted tokens (its own tier)."""
    return WRITE_MULT_1H if wt_i == "1h" else WRITE_MULT_5M


def billing_waste(waste_tok: int, wt_i) -> float:
    """$-units overpaid: re-written at rewrite_mult instead of read at 0.1.  1.90x(1h)/1.15x(5m)."""
    return waste_tok * (rewrite_mult(wt_i) - READ_MULT)


def limit_waste(waste_tok: int, wt_i, read_w: float, creation_w_mode: str) -> float:
    """
    Max-LIMIT units overpaid (PLAN §2b scenario band).
      read_w           : 0.0 (excluded from limit) | 0.1
      creation_w_mode  : 'raw' (1.0) | 'billing' (rewrite_mult)
    """
    creation_w = 1.0 if creation_w_mode == "raw" else rewrite_mult(wt_i)
    return waste_tok * (creation_w - read_w)


def cold_state(read_i: int, prev_cached: int) -> str:
    if prev_cached <= 0:
        return "NA"
    frac = read_i / prev_cached
    if frac >= WARM_READ_FRAC:
        return "WARM"
    if frac < COLD_READ_FRAC:
        return "COLD"
    return "PARTIAL"


def is_wait_attributable(gap_s: float, protecting_ttl: str) -> bool:
    """PLAN §3 wait-attribution gate: gap must STRICTLY exceed the protecting TTL.
    TTL is a *minimum* lifetime, so a cold rebuild below TTL is eviction/other, not a wait;
    strictness keeps the headline a clean conservative lower bound (codex code-review F3)."""
    if protecting_ttl not in TTL_SECONDS:
        return False  # ttl_unknown -> not headline
    return gap_s > TTL_SECONDS[protecting_ttl]


def thread_key(entry: dict):
    """Conversation-thread identity: main chain vs each sidechain. Ancestry must not cross it
    (a sidechain root's parentUuid points at the spawning main turn) — codex code-review F1."""
    return (bool(entry.get("isSidechain")), entry.get("agentId"))


# ---------------------------------------------------------------------------
# Compaction / ambiguity classification (PLAN §4.1 + §3 sensitivity band)
# ---------------------------------------------------------------------------
def compaction_kind(prev_cached, cur_cached, read_i, has_marker: bool) -> str:
    """
    Returns one of:
      'marker'      - explicit compaction marker present -> EXCLUDE from headline
      'hard_shrink' - cached_bytes shrank below HARD_SHRINK -> EXCLUDE (compaction)
      'audit_shrink'- moderate shrink 0.60..0.85 -> AMBIGUOUS (sensitivity band)
      'struct_grow' - cold + grew beyond STRUCT_GROWTH -> AMBIGUOUS (sensitivity band)
      'none'        - clean
    """
    if has_marker:
        return "marker"
    if prev_cached <= 0:
        return "none"
    ratio = cur_cached / prev_cached
    if ratio < HARD_SHRINK:
        return "hard_shrink"
    if AUDIT_SHRINK_LO <= ratio <= AUDIT_SHRINK_HI:
        return "audit_shrink"
    is_cold = (read_i < COLD_READ_FRAC * prev_cached)
    if is_cold and ratio > STRUCT_GROWTH:
        return "struct_grow"
    return "none"


# Headline = conservative lower bound. These kinds never enter the headline.
EXCLUDE_FROM_HEADLINE = {"marker", "hard_shrink"}
AMBIGUOUS_KINDS       = {"audit_shrink", "struct_grow"}  # sensitivity-band upper bound only


def classify_event(prev_uf, cur_uf, gap_s, prev_wt_carry, has_marker, model_prev, model_cur):
    """
    Full per-resume classification. Returns a dict describing the event and where its
    waste lands (headline 'wait_waste', 'nonwait_cold', 'ambiguous', 'model_switch', 'ttl_unknown').
    prev_wt_carry: carried-forward protecting TTL ('1h'|'5m'|None) — last write tier <= i-1.
    """
    prev_cached = cached_bytes(prev_uf)
    cur_cached  = cached_bytes(cur_uf)
    read_i      = cur_uf["read"]
    wt_i        = write_type(cur_uf)
    w           = waste_tokens(prev_cached, cur_cached, read_i)
    state       = cold_state(read_i, prev_cached)

    ev = {
        "prev_cached": prev_cached, "cur_cached": cur_cached, "read_i": read_i,
        "waste_tokens": w, "write_type_i": wt_i, "protecting_ttl": prev_wt_carry,
        "cold_state": state, "gap_s": gap_s, "bucket": gap_bucket_min(gap_s / 60.0),
        "invariant_ok": read_i <= prev_cached + 1,  # +1 slack for rounding
    }

    # 1) model switch — per-model cache; switch-induced cold is not wait-induced
    if model_prev is not None and model_cur is not None and model_prev != model_cur:
        ev["lane"] = "model_switch"; return ev

    # 2) compaction / ambiguity
    ck = compaction_kind(prev_cached, cur_cached, read_i, has_marker)
    ev["compaction_kind"] = ck
    if ck in EXCLUDE_FROM_HEADLINE:
        ev["lane"] = "compaction"; return ev

    # 3) unknown protecting tier
    if prev_wt_carry not in TTL_SECONDS:
        ev["lane"] = "ttl_unknown"; return ev

    # 4) wait-attribution gate
    if not is_wait_attributable(gap_s, prev_wt_carry):
        ev["lane"] = "nonwait_cold"; return ev

    # 5) ambiguous (audit) -> sensitivity band, not headline
    if ck in AMBIGUOUS_KINDS:
        ev["lane"] = "ambiguous"; return ev

    ev["lane"] = "wait_waste"   # HEADLINE
    return ev


# ---------------------------------------------------------------------------
# Wait-type classification (PLAN §4.4) — pure fn of prev turn's tool_uses + flags
# ---------------------------------------------------------------------------
def classify_wait(prev_tool_uses, prev_flags) -> str:
    names = [t.get("name", "") for t in (prev_tool_uses or [])]
    flags = prev_flags or {}

    def has(n): return n in names

    if has("Task") or has("Agent"):
        st = ""
        for t in prev_tool_uses:
            if t.get("name") in ("Task", "Agent"):
                st = (t.get("input", {}) or {}).get("subagent_type", "") or ""
                break
        s = st.lower()
        if "codex" in s: return "codex"
        if "review" in s: return "review"
        if "fable" in s: return "fable"
        return "subagent"
    if has("Workflow") or flags.get("pendingWorkflowCount", 0):
        return "workflow"
    if any(n in names for n in ("SendMessage", "TaskCreate", "TaskUpdate", "Monitor", "TaskOutput")):
        return "team"
    if flags.get("pendingBackgroundAgentCount", 0):
        return "background-agent"
    if has("Skill"):
        for t in prev_tool_uses:
            if t.get("name") == "Skill":
                sk = json.dumps(t.get("input", {})).lower()
                if "codex" in sk or "security" in sk: return "review"
    if has("Bash"):
        for t in prev_tool_uses:
            if t.get("name") == "Bash":
                cmd = ((t.get("input", {}) or {}).get("command", "") or "").lower()
                if "glab" in cmd or "gh " in cmd or cmd.startswith("gh"): return "pipeline-mr"
                if "codex" in cmd: return "codex"
        return "bash-other"
    if names:
        return "local-tool"
    return "between-turns"


# ---------------------------------------------------------------------------
# Gap bucketing & weighting helpers
# ---------------------------------------------------------------------------
def gap_bucket_min(gap_min: float) -> str:
    for lo, hi in GAP_BUCKETS_MIN:
        if lo <= gap_min < hi:
            return f"{lo}-{'inf' if hi == float('inf') else hi}m"
    return "?"


def model_base_price(model: str):
    return MODEL_BASE_PRICE.get(model, DEFAULT_BASE_PRICE)


def billing_waste_usd(waste_tok: int, wt_i, model: str) -> float:
    """$-reference waste = billing_waste (in input-equivalent tokens) * base input $/token."""
    base_in, _ = model_base_price(model)
    return billing_waste(waste_tok, wt_i) * base_in / 1_000_000.0


# ---------------------------------------------------------------------------
# Keep-alive ping break-even (PLAN §4.5C) — analysis only, nothing is built
# ---------------------------------------------------------------------------
def ping_breakeven(gap_min: float, prev_cached: int, wt_i):
    """Returns (billing: net $-units saved by pinging, quota: net quota-units saved)."""
    n_pings = max(1, math.ceil(gap_min / 4.0))
    ping_cost_billing = n_pings * prev_cached * READ_MULT
    # if pinging keeps it warm, the rebuild is avoided -> billing_waste saved
    saved_billing = billing_waste(prev_cached, wt_i)  # full prefix would have rebuilt
    net_billing = saved_billing - ping_cost_billing
    # quota: reads are excluded -> ping costs ~0 quota; rebuild quota saved
    net_quota = prev_cached  # creation tokens that would have counted (read_w=0 scenario)
    return {"n_pings": n_pings, "net_billing": net_billing, "net_quota": net_quota,
            "ping_cost_billing": ping_cost_billing, "saved_billing": saved_billing}


# ---------------------------------------------------------------------------
# IO + thread reconstruction (PLAN §4.1/§4.2) — not pure, kept thin & separate
# ---------------------------------------------------------------------------
def _resolve_config_dirs(env=None):
    """Decide which config dirs to scan, env-driven with a safe public default.

    - Default: just the standard `.claude` (relative to home).
    - `CLAUDE_CONFIG_DIR`: Claude Code's documented config-dir override. Split on COMMA
      only (a single value is the common case; comma supports multi). NOT os.pathsep —
      on POSIX that is ":", which would corrupt an absolute path. When set, REPLACES default.
    - `CC_COACH_CONFIG_DIRS`: opt-in EXTRA dirs (comma-separated, absolute or
      relative-to-home) appended to whatever the above resolved.

    Returns a list of dir tokens (absolute or relative-to-home); realpath-dedup at the
    file level (discover_files) collapses any overlaps.
    """
    env = env if env is not None else os.environ

    def split_csv(value):
        return [p.strip() for p in (value or "").split(",") if p.strip()]

    dirs = split_csv(env.get("CLAUDE_CONFIG_DIR"))
    if not dirs:
        dirs.append(".claude")
    dirs.extend(split_csv(env.get("CC_COACH_CONFIG_DIRS")))
    return dirs


def discover_files(config_dirs=None, home=None, env=None):
    """Realpath-dedup .jsonl session logs across (possibly symlinked) config dirs. PLAN §1.

    config_dirs defaults to env-resolved dirs (see _resolve_config_dirs). Each token is
    used as-is if absolute, else joined under `home`.
    """
    home = home or os.path.expanduser("~")
    if config_dirs is None:
        config_dirs = _resolve_config_dirs(env)
    seen, files = set(), []
    for d in config_dirs:
        base = d if os.path.isabs(d) else os.path.join(home, d)
        for f in glob.glob(os.path.join(base, "projects", "**", "*.jsonl"), recursive=True):
            rp = os.path.realpath(f)
            if rp in seen:
                continue
            seen.add(rp)
            files.append(rp)
    return files


def _is_writable_dir(d) -> bool:
    """True iff we can create `d` and write a probe file in it. Uses a real write +
    OSError catch (NOT os.access, which races [TOCTOU] and reads the real-uid bit)."""
    try:
        os.makedirs(d, exist_ok=True)
        # mkstemp: unique name + O_EXCL|O_CREAT, mode 0600 — never follows/truncates a
        # planted symlink or pre-existing file at a fixed path.
        fd, probe = tempfile.mkstemp(prefix=".cc_coach_probe_", dir=d)
        os.close(fd)
    except OSError:
        return False
    try:
        os.remove(probe)
    except OSError:
        pass
    return True


def open_local_write(path):
    """Open a LOCAL-ONLY artifact for writing at mode 0600 FROM CREATION — no umask race, no
    symlink follow — and return a text-mode handle. The dataset (turns/sessions/tools/meta)
    carries real paths, project names, timestamps and prompt-derived metadata, so EVERY file
    under out_dir() must be 0600, not just sessions.jsonl. Mirrors signals._write_local_json's
    hardening: O_NOFOLLOW refuses a pre-planted symlink at `path` (fail-closed), and fchmod
    re-tightens a file that pre-existed at a looser mode (O_CREAT without O_EXCL won't)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        os.close(fd)
        raise
    return os.fdopen(fd, "w")


def session_id(path):
    """Opaque, stable handle for a session log: `sess_` + 10 hex of sha1(realpath). This is the
    SHAREABLE pack's source_ref AND the LOCAL-ONLY source_index key — the raw filename can embed a
    project/client name or a username, so it must never appear in signal_pack.json. errors=replace
    keeps a surrogate-escaped (undecodable) filename from raising. Mirrors signals._proj_id."""
    return "sess_" + hashlib.sha1(str(path).encode("utf-8", errors="replace")).hexdigest()[:10]


def out_dir():
    """Resolve the dir that holds dataset/ + signal_pack.json + source_index.json + project_index.json.

    Precedence (so every script in the SAME copy agrees — base is relative to THIS
    file's location, and the skill bundles all scripts together):
      1. $CC_COACH_OUT (verbatim, created if needed)
      2. the script-adjacent base (parent of the scripts dir) IF writable (dev tree)
      3. ${XDG_CACHE_HOME:-~/.cache}/cc-usage-coach/
    """
    env_out = os.environ.get("CC_COACH_OUT")
    if env_out:
        d = os.path.abspath(os.path.expanduser(env_out))
        os.makedirs(d, exist_ok=True)
        return d
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _is_writable_dir(base):
        return base
    cache_root = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    cache = os.path.join(cache_root, "cc-usage-coach")
    os.makedirs(cache, exist_ok=True)
    return cache


def project_of(path: str) -> str:
    return os.path.basename(os.path.dirname(path))


def parse_iso(ts: str):
    if not ts:
        return None
    try:
        import datetime as dt
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def iter_entries(path: str):
    """Yield parsed JSON objects from a JSONL file, skipping malformed lines."""
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception:
        return


def tool_uses_of(msg: dict):
    out = []
    cont = (msg or {}).get("content")
    if isinstance(cont, list):
        for b in cont:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                out.append({"name": b.get("name"), "input": b.get("input", {})})
    return out


def has_compaction_marker(entry: dict, prev_entry: dict | None) -> bool:
    if entry.get("isCompactSummary") or ("compactMetadata" in entry):
        return True
    # logicalParentUuid discontinuity: a re-root that doesn't follow the prior uuid
    lpu = entry.get("logicalParentUuid")
    if lpu and prev_entry is not None and lpu != prev_entry.get("uuid") and entry.get("parentUuid") != prev_entry.get("uuid"):
        return True
    return False


def is_error_or_empty(entry: dict) -> bool:
    if entry.get("isApiErrorMessage"):
        return True
    msg = entry.get("message") or {}
    sr = msg.get("stop_reason") or entry.get("stopReason")
    if sr in ("error", "overloaded_error"):
        return True
    return False
