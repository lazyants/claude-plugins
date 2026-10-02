"""
lib_sessions.py — shared usage normalization, session discovery and local artifact IO
for the cc-usage-coach extractor, signal pack builder and arc reader.
"""
from __future__ import annotations
import hashlib, json, os, glob, tempfile


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


def write_type(uf: dict):
    """'1h' | '5m' | None (None when the turn wrote nothing — read-only)."""
    if uf["creation"] <= 0:
        return None
    if uf["c1"] > 0 and uf["c5"] == 0:
        return "1h"
    if uf["c5"] > 0 and uf["c1"] == 0:
        return "5m"
    if uf["c1"] > 0 and uf["c5"] > 0:
        return "both"
    return None


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
    """Realpath-dedup .jsonl session logs across (possibly symlinked) config dirs.

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
