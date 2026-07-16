#!/usr/bin/env python3
"""resolve_codex_companion.py -- deterministic fail-fast resolver of the installed
codex-companion.mjs path (#198, v1.4.7; PLAN-198 §2.2).

Run ONCE by the orchestrator at W5 start, BEFORE instantiating the mass-translate
Workflow template. Prints `{"companion_path": "<raw path>"}` + exit 0 on success; a
diagnostic on stderr + a nonzero exit on any failure (zero candidates / unusable node /
unusable companion / unsafe path), so the orchestrator ABORTS W5 (codex is the required
engine, R1) instead of silently hanging with no draft.

PLUGIN-anchored: it must run from the install path and GLOB install locations to find the
newest companion -- it is NEVER copied to a durable_root (like profile_validate.py), and is
NOT a bundle member.

Steps:
  1. Enumerate installed codex-companion.mjs under ~/.claude*/plugins/cache/openai-codex/**
     and pick the newest by SEMANTIC version of the .../codex/<ver>/... path segment
     (numeric, not lexical -- 1.0.10 > 1.0.9).
  2. Injection-safe path handling: require an ABSOLUTE path whose basename is
     codex-companion.mjs; REJECT only a single-quote / any control char / newline / NUL
     (the two carriers: a single-quoted shell arg + a json.dumps JS literal). Spaces and
     non-ASCII are ACCEPTED (a legitimate /Users/José/... or /Users/My Name/... install).
  3. Validate node runnable + companion present + `node <companion> status --cwd <root>
     --all --json` exits 0 -- under a FINITE stdlib subprocess timeout (no unbounded call).
  4. Print + exit 0, else stderr diagnostic + nonzero.

stdlib-only.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys

DEFAULT_STATUS_TIMEOUT_SEC = 30

# .../plugins/cache/openai-codex/codex/<ver>/scripts/codex-companion.mjs
_VER_RE = re.compile(r"[/\\]openai-codex[/\\]codex[/\\]([^/\\]+)[/\\]")


def default_globs():
    home = os.path.expanduser("~")
    # Every Claude config profile (~/.claude, ~/.claude2, ~/.claude-bm, ...) shares one
    # symlinked plugins store, but glob each so a non-symlinked layout is still covered.
    return [os.path.join(home, ".claude*", "plugins", "cache", "openai-codex",
                         "**", "codex-companion.mjs")]


def _version_key(path):
    """Numeric semver tuple of the .../codex/<ver>/... segment; () if unparseable
    (sorts lowest, so a well-formed version always wins)."""
    m = _VER_RE.search(path)
    if not m:
        return ()
    return tuple(int(x) for x in re.findall(r"\d+", m.group(1)))


def enumerate_companions(globs):
    found = set()
    for pat in globs:
        for hit in glob.glob(pat, recursive=True):
            if os.path.isfile(hit):
                found.add(os.path.abspath(hit))
    return sorted(found)


def pick_newest(paths):
    if not paths:
        return None
    # Highest semver first; ties broken by path string for determinism.
    return sorted(paths, key=lambda p: (_version_key(p), p))[-1]


def path_is_safe(path):
    """Absolute + basename codex-companion.mjs + free of the carrier-breaking chars.
    Returns None if safe, else a reason string."""
    if not os.path.isabs(path):
        return "companion path is not absolute"
    if os.path.basename(path) != "codex-companion.mjs":
        return "companion path does not end in /codex-companion.mjs"
    for ch in path:
        o = ord(ch)
        if ch == "'":
            return "companion path contains a single quote (unsupported)"
        if o < 0x20 or o == 0x7f:
            return "companion path contains a control character"
    return None


def companion_runnable(node, companion, root, timeout_sec):
    """True iff `node <companion> status --cwd <root> --all --json` exits 0 within the
    finite timeout (proves node + companion + job-store reachable from the run's cwd)."""
    try:
        proc = subprocess.run(
            [node, companion, "status", "--cwd", root, "--all", "--json"],
            capture_output=True, text=True, timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return False, "companion `status` call timed out after %ss" % timeout_sec
    except (OSError, ValueError) as exc:
        return False, "could not run node/companion (%s)" % exc
    if proc.returncode != 0:
        return False, "companion `status` exited %s: %s" % (proc.returncode, proc.stderr.strip())
    return True, None


def resolve(durable_root, globs, node="node", timeout_sec=DEFAULT_STATUS_TIMEOUT_SEC):
    """Return (raw_path, None) on success or (None, reason)."""
    candidates = enumerate_companions(globs)
    if not candidates:
        return None, "no codex-companion.mjs found under: %s" % "; ".join(globs)
    chosen = pick_newest(candidates)
    unsafe = path_is_safe(chosen)
    if unsafe:
        return None, "unsafe companion path (%s): %s" % (unsafe, chosen)
    okrun, why = companion_runnable(node, chosen, durable_root, timeout_sec)
    if not okrun:
        return None, why
    return chosen, None


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="resolve_codex_companion.py",
        description="Resolve the newest installed codex-companion.mjs path (#198).",
    )
    p.add_argument("--durable-root", required=True, dest="durable_root")
    p.add_argument("--search-glob", action="append", default=None, dest="search_glob",
                   help="Override the default install-location globs (repeatable).")
    p.add_argument("--node", default="node")
    p.add_argument("--timeout-sec", type=int, default=DEFAULT_STATUS_TIMEOUT_SEC,
                   dest="timeout_sec")
    args = p.parse_args(argv)

    globs = args.search_glob if args.search_glob else default_globs()
    timeout_sec = args.timeout_sec if args.timeout_sec > 0 else DEFAULT_STATUS_TIMEOUT_SEC
    raw, reason = resolve(args.durable_root, globs, node=args.node, timeout_sec=timeout_sec)
    if raw is None:
        print("resolve_codex_companion: %s" % reason, file=sys.stderr)
        return 1
    print(json.dumps({"companion_path": raw}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
