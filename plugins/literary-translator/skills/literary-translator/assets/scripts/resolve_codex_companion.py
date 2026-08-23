#!/usr/bin/env python3
"""resolve_codex_companion.py -- deterministic fail-fast resolver of the installed
codex-companion.mjs path (#198, v1.4.7; PLAN-198 §2.2).

Run ONCE by the orchestrator at W5 start, BEFORE instantiating the mass-translate
Workflow template. Prints `{"companion_path": "<raw path>"}` + exit 0 on success; a
diagnostic on stderr + a nonzero exit on any failure (zero candidates / unusable node /
unusable companion / unsafe path), so the orchestrator ABORTS W5 (codex is the required
engine, R1) instead of silently hanging with no draft.

LOCATION-INDEPENDENT: this file reads no `__file__` and imports nothing plugin-specific.
Its DEFAULT search (`--search-glob` overrides it, and can root it anywhere) is
rooted at the RUNNING Claude config profile (`CLAUDE_CONFIG_DIR`, else `~/.claude`)
and then at `os.path.expanduser("~")` against
`~/.claude*/plugins/cache/openai-codex/**/codex-companion.mjs` -- a DIFFERENT plugin's own
install cache, globbed identically regardless of where this file happens to be running
from. Its own location never enters that search, which is the property the copy rule below
rests on; the search root is an ENVIRONMENT fact, never `__file__`. It is therefore
copied to `${durable_root}/scripts/` by Step 0a like every other
self-anchored script, and BOTH call sites are legitimate: SKILL.md's W5 instantiation step
(1.4.7) runs the PLUGIN copy, because the orchestrating session already holds
`{{PLUGIN_ROOT}}` at that point, while segment_dispatch_driver.py's own dispatch path runs
the durable copy, because a self-anchored driver invocation has no plugin root to run from.

This docstring used to say the opposite -- "PLUGIN-anchored: it must run from the install
path ... NEVER copied to a durable_root" -- and that sentence was the ONLY basis for
excluding this file from Step 0a's copy pass. It was false, and the exclusion it justified
left SKILL.md's own documented self-anchored driver launch unable to dispatch a single
segment: the driver resolved this script under `${durable_root}/scripts/`, where nothing
had ever copied it, and fataled before rendering any prompt. Do not re-derive that argument
-- the property is pinned by a test: `tests/resolve_codex_companion.test.py`'s
`test_the_resolver_contains_no_executable_reference_to_dunder_file` parses this file and
fails if any CODE here ever reads `__file__`. Read that test's verdict, not a raw
`grep __file__` over this file: the mentions in this docstring are prose ABOUT the claim
and a text search cannot tell them apart from a real one.

Still NOT a `PLUGIN_BUNDLE_MEMBERS` entry (see cache_key.py), like draft_ready.py and the
other Step-0a-copied siblings outside that allowlist: the companion path this resolves is a
per-machine ENVIRONMENT fact, deliberately kept out of every bundle hash and out of the
resume digest (see references/ledger-and-resumability.md).

Steps:
  1. Enumerate installed codex-companion.mjs in the RUNNING profile's own store first
     (`$CLAUDE_CONFIG_DIR`, else `~/.claude`), falling back to
     ~/.claude*/plugins/cache/openai-codex/** only when that profile holds none; within
     whichever tier answers, pick the newest by SEMANTIC version of the .../codex/<ver>/...
     path segment (numeric, not lexical -- 1.0.10 > 1.0.9).

     WHY THE PROFILE COMES FIRST (#287). Each profile now owns a REAL, independent
     `plugins/` directory -- the `skills`/`memory`/`agents` trees stay shared symlinks, but
     `plugins/` does not. A comment here used to assert the opposite ("shares one symlinked
     plugins store"), and that false premise was the entire reason a cross-profile tie-break
     was considered harmless: it was read as picking between four NAMES for one file. It is
     picking between four independent files. Measured 2026-07-22 in this operator's
     environment: eight candidates across `.claude`, `.claude2`, `.claude3` and `.claude-bm`,
     the four 1.0.6 copies tying on version, `.claude3` winning on LEXICAL PATH ORDER while
     the session ran under `.claude2`. Consequences that needed no attacker: a
     `claude plugin update codex@openai-codex` in ANY profile silently rebinds W5 in all of
     them, and an operator PINNING an older companion in the active profile is silently
     defeated by newest-wins across foreign ones. The running profile now wins OUTRIGHT --
     even over a newer foreign version, because an explicit local pin is a decision and a
     foreign profile's contents are not.
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


def running_profile_dir():
    """The Claude config profile directory this process is running under, or None if
    there is no usable one.

    MIRRORS THE CLI'S OWN EXPRESSION AND ADDS NOTHING. The installed `claude` binary
    computes `CLAUDE_CONFIG_DIR ?? path.join(homedir(), ".claude")` -- nullish, so only an
    ABSENT variable falls back to `~/.claude`, and a present value is used VERBATIM: no
    whitespace trimming, no `~` expansion. Every normalization added here would be a silent
    divergence from the authority this claims to follow, and each one can name a DIFFERENT
    directory than the session actually runs in -- which is the whole defect class (#287).

    Returns `(config_dir, problem)`; exactly one of the two is None. A non-ABSOLUTE value
    is a problem, NOT a skipped tier. Globbing a relative value would resolve it against
    THIS script's cwd, which is not the cwd the CLI resolved it against -- but skipping the
    tier and letting the cross-profile fallback answer is no better: it silently binds a
    FOREIGN profile's companion, which is precisely the defect this preference exists to
    stop. It is also the fail-fast contract `resolve()` states below: a tier is skipped only
    when it holds ZERO CANDIDATES, never because validating it failed. So the caller aborts
    with this reason instead.
    """
    raw = os.environ.get("CLAUDE_CONFIG_DIR")
    if raw is None:
        default = os.path.join(os.path.expanduser("~"), ".claude")
        if not os.path.isabs(default):
            return None, ("CLAUDE_CONFIG_DIR is unset and the ~/.claude fallback did not "
                          "expand to an absolute path (%r) -- HOME is unset or relative, "
                          "so the running Claude config profile cannot be identified"
                          % default)
        return default, None
    if not os.path.isabs(raw):
        return None, ("CLAUDE_CONFIG_DIR is set to a non-absolute path (%r), so the running "
                      "Claude config profile cannot be identified. Resolving it against "
                      "this script's cwd would name a different directory than the CLI "
                      "resolved it against, and falling back across profiles would bind a "
                      "FOREIGN profile's companion (#287). Set it to an absolute path, or "
                      "unset it to use ~/.claude." % raw)
    return raw, None


def _store_glob(config_dir):
    """The openai-codex install-cache glob under ONE config profile.

    `glob.escape` is load-bearing, not defensive noise: `CLAUDE_CONFIG_DIR` is an
    operator-authored path, and a legitimate one containing `[`, `*` or `?` would otherwise
    be read as a PATTERN and could match a DIFFERENT profile's store -- reproducing the very
    binding this preference exists to stop. The `**` is appended AFTER the escape, so it
    stays recursive.
    """
    return os.path.join(glob.escape(config_dir), "plugins", "cache", "openai-codex",
                        "**", "codex-companion.mjs")


def default_glob_tiers():
    """Ordered tiers: the RUNNING profile's own store first, then every profile.

    The cross-profile tier is unchanged from before #287 and is deliberately still a
    `.claude*` wildcard: profiles no longer share one symlinked plugins store (each owns a
    real, independent `plugins/` directory), so it enumerates several distinct files rather
    than several names for one.

    Returns `(tiers, problem, note)`. `problem` aborts; `note` is extra context for the
    not-found message when a tier had to be left out. A profile that cannot be identified is
    reported, never quietly dropped to the cross-profile tier.

    The cross-profile ROOT needs the same absoluteness guard the running profile got, for
    the same reason: `os.path.expanduser("~")` returns its argument UNCHANGED when HOME is
    unset or relative, so the tier becomes a cwd-relative glob and `glob` will happily bind
    a foreign companion found under whatever directory this script was launched from. That
    tier is therefore dropped rather than searched -- dropping it can only ever REFUSE,
    never bind the wrong file, so unlike an unusable running profile it does not have to
    abort a run whose own profile tier can still answer.
    """
    running, problem = running_profile_dir()
    if problem:
        return None, problem, None
    home = os.path.expanduser("~")
    tiers = [[_store_glob(running)]]
    if not os.path.isabs(home):
        return tiers, None, (
            "the cross-profile fallback was NOT searched: HOME is unset or relative (%r), "
            "so `~/.claude*` is not an absolute root and globbing it would resolve against "
            "this script's cwd and could bind a FOREIGN profile's companion (#287)" % home
        )
    tiers.append([os.path.join(home, ".claude*", "plugins", "cache", "openai-codex",
                               "**", "codex-companion.mjs")])
    return tiers, None, None


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


def resolve(durable_root, glob_tiers, node="node", timeout_sec=DEFAULT_STATUS_TIMEOUT_SEC,
            note=None):
    """Return (raw_path, None) on success or (None, reason).

    `glob_tiers` is an ORDERED list of glob-lists. The first tier that yields any candidate
    decides; `pick_newest()` then chooses within THAT tier, so a foreign profile's newer
    companion never outranks the running profile's own.

    A TIER FALLS THROUGH ONLY ON ZERO CANDIDATES -- NEVER ON A VALIDATION FAILURE. Once a
    tier yields candidates, an unsafe path or a failing `status` call ABORTS with that
    reason; the next tier is not consulted. Retrying the cross-profile tier there would
    hand back a green run on a foreign binary, which is the defect this preference exists
    to stop, one step later (#287). It is also the pre-existing fail-fast contract:
    segment_dispatch_driver.py turns any nonzero exit from this script into
    `fatal(..., exit_code=2)`.
    """
    for tier in glob_tiers:
        candidates = enumerate_companions(tier)
        if not candidates:
            continue
        chosen = pick_newest(candidates)
        unsafe = path_is_safe(chosen)
        if unsafe:
            return None, "unsafe companion path (%s): %s" % (unsafe, chosen)
        okrun, why = companion_runnable(node, chosen, durable_root, timeout_sec)
        if not okrun:
            return None, why
        return chosen, None
    # Reachable only when EVERY tier hit the `continue` above, so the tiers
    # searched are always all of them -- no accumulator needed to say so.
    searched = "; ".join(pattern for tier in glob_tiers for pattern in tier)
    if note:
        return None, "no codex-companion.mjs found under: %s (%s)" % (searched, note)
    return None, "no codex-companion.mjs found under: %s" % searched


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="resolve_codex_companion.py",
        description="Resolve the codex-companion.mjs path for the running Claude config "
                    "profile, newest-semver within it (#198, #287).",
    )
    p.add_argument("--durable-root", required=True, dest="durable_root")
    p.add_argument("--search-glob", action="append", default=None, dest="search_glob",
                   help="Override the default install-location globs (repeatable). Repeats "
                        "form ONE union tier resolved by newest-semver, not a priority "
                        "order, and the running-profile preference does not apply.")
    p.add_argument("--node", default="node")
    p.add_argument("--timeout-sec", type=int, default=DEFAULT_STATUS_TIMEOUT_SEC,
                   dest="timeout_sec")
    args = p.parse_args(argv)

    # An explicit override is fully authoritative: ONE tier holding every repeat, so its
    # semantics are byte-identical to before #287 and the profile preference cannot reach
    # it. The suite relies on that -- it drives this script with --search-glob precisely so
    # the real ~/.claude* store is never touched.
    if args.search_glob:
        tiers, problem, note = [args.search_glob], None, None
    else:
        tiers, problem, note = default_glob_tiers()
    if problem:
        print("resolve_codex_companion: %s" % problem, file=sys.stderr)
        return 1
    timeout_sec = args.timeout_sec if args.timeout_sec > 0 else DEFAULT_STATUS_TIMEOUT_SEC
    raw, reason = resolve(args.durable_root, tiers, node=args.node, timeout_sec=timeout_sec,
                          note=note)
    if raw is None:
        print("resolve_codex_companion: %s" % reason, file=sys.stderr)
        return 1
    print(json.dumps({"companion_path": raw}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
