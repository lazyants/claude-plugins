#!/usr/bin/env python3
"""arc.py — compact, LOCAL-ONLY single-session digest (cc-usage-coach step 4).
Prints to stdout the human-prompt ARC + structural markers of ONE session, with common
filesystem-path forms redacted best-effort (NOT a guarantee). Output contains the user's
prompt text and is LOCAL-ONLY, never
written to a shareable file. The realpath is looked up from source_index.json by the
opaque source_ref and NEVER printed."""
import sys, os, re, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_sessions as L


# ---------------------------------------------------------------------------
# Path redaction (security-critical). Regex FIRST, then literal home — order
# matters so a mangled `-Users-x-a-b` collapses whole before the home-literal
# pass would only catch a prefix.
#
# The char-class `[^"'`\r\n]+` stops only at a quote or line end — NOT at a space
# (codex F1): a path with spaces ("/Users/x/My Project/f") must be fully redacted.
# This consumes any same-line trailing prose after the path too; over-redaction is
# the safe direction (a leak is not), so that trade is accepted for these patterns.
# ---------------------------------------------------------------------------
_PATH_TAIL = r"[^\"'`\r\n]+"
_PATH_RES = [
    re.compile(r"/Users/" + _PATH_TAIL),
    re.compile(r"/home/" + _PATH_TAIL),
    re.compile(r"-Users-" + _PATH_TAIL),
    re.compile(r"-home-" + _PATH_TAIL),
    re.compile(r"%2[fF][Uu]sers%2[fF]" + _PATH_TAIL),
    re.compile(r"%2[fF]home%2[fF]" + _PATH_TAIL),
    # Windows path (public tool runs cross-platform): optional drive + \Users\...
    re.compile(r"(?:[A-Za-z]:)?\\Users\\" + _PATH_TAIL),
    # NOTE: a bare `~/...` is intentionally NOT redacted — it reveals no machine info
    # and a `~[/\\]` rule would over-redact ordinary prose (codex F1, lead revision).
]


def redact_paths(text):
    if not text:
        return text
    s = text
    for rx in _PATH_RES:
        s = rx.sub("<path>", s)
    home = os.path.expanduser("~")
    if home and home != "~":
        s = s.replace(home, "<path>").replace(home.replace("/", "-"), "<path>")
    return s


# ---------------------------------------------------------------------------
# Text extraction + tag stripping + command capture
# ---------------------------------------------------------------------------
_CMD_RE = re.compile(r"<command-name>\s*([^<]+?)\s*</command-name>", re.S)
_TAG_STRIP_RE = re.compile(
    r"<(command-name|command-message|command-args|local-command-stdout|system-reminder|bash-input|bash-stdout|bash-stderr)>.*?</\1>",
    re.S)
_RESUME_BANNER = "This session is being continued from a previous conversation"


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text") or "" for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _is_user(e):
    etype = e.get("type")
    role = (e.get("message") or {}).get("role")
    return etype == "user" or (etype is None and role == "user")


def iter_human_prompts(entries):
    """Human-authored prompts only: skip meta/compact/tool-result turns, strip command
    and reminder tags, collapse whitespace, redact paths, truncate to 200 chars."""
    out = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            continue
        if e.get("isMeta") or e.get("isCompactSummary") or ("compactMetadata" in e):
            continue
        if not _is_user(e):
            continue
        raw = _text_of((e.get("message") or {}).get("content"))
        if not raw or not raw.strip():
            continue
        m = _CMD_RE.search(raw)
        cmd = redact_paths(m.group(1).strip()) if m else None
        cleaned = _TAG_STRIP_RE.sub("", raw)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned and not cmd:
            continue
        text = redact_paths(cleaned)[:200]
        out.append({"idx": i, "ts": e.get("timestamp"), "cmd": cmd, "text": text})
    return out


def arc_markers(entries):
    """Structural markers across the session: compactions, continuation resumes,
    autonomous-loop sentinel, and a tally of slash commands."""
    markers = {
        "compactions": 0,
        "continuation_resumes": 0,
        "autonomous_loop": False,
        "slash_cmds": {},
    }
    prev = None
    for e in entries:
        if not isinstance(e, dict):
            continue
        if L.has_compaction_marker(e, prev):
            markers["compactions"] += 1
        prev = e

    for p in iter_human_prompts(entries):
        cmd = p["cmd"]
        if cmd:
            markers["slash_cmds"][cmd] = markers["slash_cmds"].get(cmd, 0) + 1
            if "/loop" in cmd:
                markers["autonomous_loop"] = True

    for e in entries:
        if not isinstance(e, dict):
            continue
        if e.get("isMeta") or e.get("isCompactSummary") or ("compactMetadata" in e):
            continue
        if not _is_user(e):
            continue
        raw = _text_of((e.get("message") or {}).get("content"))
        if not raw:
            continue
        # strip command/reminder/stdout tags first so the banner/sentinel are only
        # counted when they appear in the HUMAN text, not in echoed reminder/stdout
        # noise (mirrors iter_human_prompts' guards — codex F6).
        scan = _TAG_STRIP_RE.sub("", raw)
        if _RESUME_BANNER in scan:
            markers["continuation_resumes"] += 1
        if "<<autonomous-loop" in scan:
            markers["autonomous_loop"] = True

    return markers


def resolve_ref(ref, index):
    return index.get(ref)


def _project_from_cwd(cwd):
    """Header 'project' = the cwd LEAF (e.g. /Users/alice/myrepo -> 'myrepo').

    But if the cwd IS a home directory itself — its parent is /Users or /home, or it
    equals the expanded home — then the leaf is the USERNAME, an identity leak. Suppress
    it (return None -> omitted from the header) in that case. Normal repos still show."""
    c = str(cwd).rstrip("/")
    if not c:
        return None
    parent = os.path.dirname(c)
    home = os.path.expanduser("~")
    if parent in ("/Users", "/home") or (home and home != "~" and c == home.rstrip("/")):
        return None
    return redact_paths(os.path.basename(c))


def _meta_from_entries(entries, ref):
    project = None
    for e in entries:
        if not isinstance(e, dict):
            continue
        cwd = e.get("cwd")
        if cwd:
            project = _project_from_cwd(cwd)
            break

    dates = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        ts = e.get("timestamp")
        if ts:
            dates.append(str(ts)[:10])
    span = (min(dates), max(dates)) if dates else (None, None)

    models = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        model = (e.get("message") or {}).get("model")
        if model and model not in models:
            models.append(model)

    return {"ref": ref, "project": project, "span": span, "models": models}


def format_arc(meta, prompts, markers, head=40, tail=10):
    d0, d1 = meta.get("span", (None, None))
    if d0 and d1:
        span_str = d0 if d0 == d1 else f"{d0}..{d1}"
    elif d0:
        span_str = d0
    else:
        span_str = None

    # defensively redact the ref too — a caller could pass a path-shaped source_ref
    # (codex F3); the opaque index keys are normally safe, but never trust the input.
    header_parts = [redact_paths(str(meta.get("ref") or "?"))]
    if meta.get("project"):
        header_parts.append(meta["project"])
    header_parts.append(f'{meta.get("turns", len(prompts))} turns')
    if span_str:
        header_parts.append(span_str)
    models = meta.get("models") or []
    if models:
        header_parts.append(",".join(models))
    header = " | ".join(header_parts)

    sc = markers.get("slash_cmds") or {}
    if sc:
        sc_str = ",".join(f"{k}:{sc[k]}" for k in sorted(sc))
    else:
        sc_str = "{}"
    marker_line = (
        f"markers: compactions={markers.get('compactions', 0)} "
        f"resumes={markers.get('continuation_resumes', 0)} "
        f"autonomous_loop={bool(markers.get('autonomous_loop'))} "
        f"slash_cmds={sc_str}"
    )

    lines = [header, marker_line]

    def fmt(p):
        line = f"[{p['idx']}]"
        if p.get("cmd"):
            line += " " + p["cmd"]
        line += " " + (p.get("text") or "")
        return line.rstrip()

    if len(prompts) > head + tail:
        n = len(prompts) - head - tail
        for p in prompts[:head]:
            lines.append(fmt(p))
        lines.append(f"[+{n} earlier prompts elided]")
        for p in prompts[-tail:]:
            lines.append(fmt(p))
    else:
        for p in prompts:
            lines.append(fmt(p))

    return "\n".join(lines)


def _run(argv):
    if len(argv) < 2:
        print("usage: arc.py <source_ref>", file=sys.stderr)
        return 2
    ref = argv[1]
    od = L.out_dir()
    idx_path = os.path.join(od, "source_index.json")
    if not os.path.exists(idx_path):
        print("source index not found — run signals.py first, or set CC_COACH_OUT",
              file=sys.stderr)
        return 1
    try:
        with open(idx_path) as fh:
            index = json.load(fh)
    except Exception:
        print("source index unreadable — re-run signals.py", file=sys.stderr)
        return 1
    path = resolve_ref(ref, index)
    if not path:
        print("unknown source_ref (not in index)", file=sys.stderr)
        return 1
    entries = list(L.iter_entries(path))
    if not entries:
        print("session is empty or unreadable", file=sys.stderr)
        return 1
    prompts = iter_human_prompts(entries)
    markers = arc_markers(entries)
    meta = _meta_from_entries(entries, ref)
    meta["turns"] = len(prompts)
    print(format_arc(meta, prompts, markers))
    return 0


def main(argv):
    """Top-level guard (codex F5): swallow ANY unexpected exception into a generic
    message + rc 1, so a stray failure never prints a Python traceback — whose frames
    would echo this script's realpath (a path leak)."""
    try:
        return _run(argv)
    except SystemExit:
        raise
    except BaseException:
        print("arc.py: could not build the digest", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
