"""Tests for assets/scripts/resolve_codex_companion.py (#198, v1.4.7; PLAN-198 §2.2/§4).

Covers: zero candidates -> nonzero; newest-semver pick (1.0.10 > 1.0.9 > 1.0.6);
node/companion `status` failing OR hanging past the finite timeout -> nonzero; success
-> {"companion_path": "<raw>"} + exit 0; injection-safety (single-quote / control / newline
/ NUL / relative / wrong-basename REJECTED); compatibility (a legitimate space / non-ASCII
path ACCEPTED, its json.dumps is a valid JS literal, and a single-quoted-bash round-trip
recovers it exactly).

The resolver is location-INDEPENDENT (it reads no `__file__`; its DEFAULT search is rooted at
the running Claude config profile and then `~` -- environment facts, never its own path), so
Step 0a copies it to `${durable_root}/scripts/` like every other self-anchored
script and it runs correctly from either place -- see its own module docstring for why the
older "plugin-anchored, never copied" claim was false. These tests drive it as a subprocess
with an explicit --search-glob (so the real ~/.claude* store is never touched) and a fake
executable `node` stub, plus white-box calls into its pure helpers; that setup is what makes
them independent of where the file sits, which is the property under test here.
"""

import ast
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
RESOLVER_SRC = SCRIPTS_DIR / "resolve_codex_companion.py"

assert RESOLVER_SRC.is_file(), f"expected the resolver at {RESOLVER_SRC}"

_spec = importlib.util.spec_from_file_location("resolve_codex_companion_mod", str(RESOLVER_SRC))
assert _spec is not None and _spec.loader is not None
rcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rcc)


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
def make_companion(base: Path, ver: str, parent: str = "profileA") -> Path:
    """Create a fake codex-companion.mjs at .../openai-codex/codex/<ver>/scripts/."""
    d = base / parent / "plugins" / "cache" / "openai-codex" / "codex" / ver / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "codex-companion.mjs"
    f.write_text("// fake companion\n", encoding="utf-8")
    return f


def write_fake_node(tmp: Path, body: str) -> str:
    node = tmp / "fake_node.py"
    node.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    node.chmod(node.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(node)


# A node that succeeds on any invocation.
FAKE_NODE_OK = "import sys\nsys.exit(0)\n"
# A node that fails (non-zero) on any invocation.
FAKE_NODE_FAIL = "import sys\nsys.stderr.write('boom')\nsys.exit(1)\n"
# A node that hangs (sleeps) so the resolver's finite timeout must fire.
FAKE_NODE_HANG = "import time\ntime.sleep(30)\n"


# #430: the default is deliberately generous. Every success-path caller here needs the
# fake node to WIN this race, so the bound only has to be far above a node spawn on a
# loaded machine -- it proves nothing by being tight, and a tight one turns ordinary
# CI load into a red that reads exactly like a resolver regression. The hang test
# passes its own small timeout_sec, so the finite-timeout property stays pinned.
def run_resolver(root: Path, glob_pat: str, node: str, timeout_sec: int = 15):
    return subprocess.run(
        [sys.executable, str(RESOLVER_SRC), "--durable-root", str(root),
         "--search-glob", glob_pat, "--node", node, "--timeout-sec", str(timeout_sec)],
        capture_output=True, text=True, timeout=60,
    )


# --------------------------------------------------------------------------- #
# white-box: pure helpers
# --------------------------------------------------------------------------- #
def test_version_key_is_numeric_not_lexical():
    a = "/x/openai-codex/codex/1.0.9/scripts/codex-companion.mjs"
    b = "/x/openai-codex/codex/1.0.10/scripts/codex-companion.mjs"
    assert rcc._version_key(b) > rcc._version_key(a)  # 1.0.10 > 1.0.9 numerically


def test_pick_newest_semver():
    paths = [
        "/x/openai-codex/codex/1.0.6/scripts/codex-companion.mjs",
        "/x/openai-codex/codex/1.0.10/scripts/codex-companion.mjs",
        "/x/openai-codex/codex/1.0.9/scripts/codex-companion.mjs",
    ]
    assert rcc.pick_newest(paths).endswith("/1.0.10/scripts/codex-companion.mjs")


@pytest.mark.parametrize("bad", [
    "rel/codex-companion.mjs",                       # not absolute
    "/abs/dir/other.mjs",                            # wrong basename
    "/abs/dir/codex-companion.mjs\n",               # newline
    "/abs/di\x00r/codex-companion.mjs",             # NUL
    "/abs/di\x07r/codex-companion.mjs",             # control char (BEL)
    "/abs/di'r/codex-companion.mjs",                # single quote
])
def test_path_is_safe_rejects(bad):
    assert rcc.path_is_safe(bad) is not None


@pytest.mark.parametrize("good", [
    "/Users/José/x/codex-companion.mjs",            # non-ASCII accepted
    "/Users/My Name/x/codex-companion.mjs",         # space accepted
    "/Users/a/codex-companion.mjs",
])
def test_path_is_safe_accepts_legit(good):
    assert rcc.path_is_safe(good) is None


# --------------------------------------------------------------------------- #
# integration: subprocess
# --------------------------------------------------------------------------- #
def test_zero_candidates_nonzero(tmp_path):
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    glob_pat = str(tmp_path / "nothing" / "**" / "codex-companion.mjs")
    proc = run_resolver(tmp_path, glob_pat, node)
    assert proc.returncode != 0
    assert "no codex-companion.mjs" in proc.stderr


def test_success_picks_newest_semver(tmp_path):
    make_companion(tmp_path, "1.0.6")
    make_companion(tmp_path, "1.0.9")
    newest = make_companion(tmp_path, "1.0.10")
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    glob_pat = str(tmp_path / "**" / "codex-companion.mjs")
    proc = run_resolver(tmp_path, glob_pat, node)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["companion_path"] == str(newest)


def test_node_status_failure_nonzero(tmp_path):
    make_companion(tmp_path, "1.0.10")
    node = write_fake_node(tmp_path, FAKE_NODE_FAIL)
    glob_pat = str(tmp_path / "**" / "codex-companion.mjs")
    proc = run_resolver(tmp_path, glob_pat, node)
    assert proc.returncode != 0
    assert "status" in proc.stderr


def test_node_hang_bounded_by_timeout(tmp_path):
    make_companion(tmp_path, "1.0.10")
    node = write_fake_node(tmp_path, FAKE_NODE_HANG)
    glob_pat = str(tmp_path / "**" / "codex-companion.mjs")
    import time
    t0 = time.monotonic()
    proc = run_resolver(tmp_path, glob_pat, node, timeout_sec=1)
    elapsed = time.monotonic() - t0
    assert proc.returncode != 0
    assert "timed out" in proc.stderr
    assert elapsed < 15  # the finite timeout fired, we did NOT wait the full 30s sleep


def test_unsafe_newest_path_rejected(tmp_path):
    """A newest candidate whose path contains a single quote is REJECTED (no fallback to an
    older safe one -- an unsafe resolved path is a hard fail-fast)."""
    make_companion(tmp_path, "1.0.6")                       # safe, older
    make_companion(tmp_path, "1.0.99", parent="pro'file")   # newest, unsafe (quote in path)
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    glob_pat = str(tmp_path / "**" / "codex-companion.mjs")
    proc = run_resolver(tmp_path, glob_pat, node)
    assert proc.returncode != 0
    assert "unsafe companion path" in proc.stderr


def test_legit_space_and_nonascii_path_accepted_and_roundtrips(tmp_path):
    """A legitimate install path with a space AND a non-ASCII char is ACCEPTED; its
    json.dumps is a valid JS string literal that recovers the exact path, and inside a
    single-quoted bash argument it survives verbatim (no injection, exact recovery)."""
    newest = make_companion(tmp_path, "1.0.7", parent="My Náme dir")
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    glob_pat = str(tmp_path / "**" / "codex-companion.mjs")
    proc = run_resolver(tmp_path, glob_pat, node)
    assert proc.returncode == 0, proc.stderr
    raw = json.loads(proc.stdout)["companion_path"]
    assert raw == str(newest)
    assert " " in raw and any(ord(c) > 127 for c in raw)

    # The orchestrator json.dumps-encodes the raw path into a JS string literal ...
    js_literal = json.dumps(raw)
    assert json.loads(js_literal) == raw            # valid, exact round-trip
    assert "'" not in raw                           # ... and the value is single-quote-safe,

    # ... so a single-quoted bash argument recovers it byte-for-byte (no word-splitting,
    # no injection). printf '%s' '<raw>' echoes the path unchanged.
    echoed = subprocess.run(
        ["bash", "-c", "printf %s " + "'" + raw + "'"],
        capture_output=True, text=True,
    )
    assert echoed.returncode == 0
    assert echoed.stdout == raw


def test_the_resolver_contains_no_executable_reference_to_dunder_file():
    """The load-bearing disproof of this release, made mechanically checkable.

    The exclusion that broke the documented self-anchored driver launch rested on one
    claim: that a durable copy of this script "could not glob the plugin's own install
    locations". The disproof is that the script's location never enters its search --
    it reads no `__file__`. That disproof is currently asserted only in prose, in four
    places, and prose about a property is not the property.

    Checks the AST, not the raw text, and the distinction is the whole point: this
    file's own module docstring now DISCUSSES `__file__` by name (it has to -- it tells
    the next maintainer exactly which claim to re-check before re-excluding this
    script), so a literal `grep -c __file__` returns a nonzero count and reads as a
    refutation of the very sentence above it. `ast.walk()` sees identifiers, never
    comments or string contents, so it answers the question actually being asked:
    does any CODE PATH here depend on where this file sits?

    If this ever fails, the resolver has become location-dependent and Step 0a copying
    it to `${durable_root}/scripts/` is no longer safe -- re-read the exclusion
    reasoning in SKILL.md's Step 0a copy-pass section before changing this test."""
    tree = ast.parse(RESOLVER_SRC.read_text(encoding="utf-8"), filename=str(RESOLVER_SRC))
    offenders = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "__file__"
    ]
    assert offenders == [], (
        f"{RESOLVER_SRC.name} now READS __file__ at line(s) {offenders} -- it is no "
        f"longer location-independent, which is the sole premise on which Step 0a "
        f"copies it into ${{durable_root}}/scripts/"
    )



# --------------------------------------------------------------------------- #
# #287: the RUNNING config profile decides the binding
#
# The cases in this section drive the resolver with NO --search-glob (the two
# override cases at the end of it are the deliberate exceptions, and the
# white-box `running_profile_dir` cases call in directly), because that is the
# only way to exercise the default tiers -- so isolation comes from the
# ENVIRONMENT instead: HOME is pointed at tmp_path (posixpath.expanduser reads $HOME), and
# CLAUDE_CONFIG_DIR is set or explicitly removed. The real ~/.claude* store is
# unreachable from any of them. A case that forgot HOME would silently bind this
# machine's own four-profile store and pass or fail for reasons unrelated to the
# code, so `run_resolver_env` never lets a caller inherit it.
# --------------------------------------------------------------------------- #
def run_resolver_env(root: Path, node: str, home: Path, config_dir, timeout_sec: int = 15,
                     search_globs=None):
    """Drive the resolver under a controlled profile env.

    `config_dir` None removes CLAUDE_CONFIG_DIR entirely (the CLI's own "absent"
    case, which falls back to ~/.claude); a string is passed through VERBATIM,
    including one that is empty or relative -- those are the cases under test.

    `search_globs` defaults to NONE, i.e. no --search-glob at all, which is what
    every default-tier case wants. Passing a list appends one --search-glob per
    entry, for the override cases; HOME and CLAUDE_CONFIG_DIR are still pinned
    there, so a preference that leaked PAST the override would have a controlled
    profile to leak to rather than this machine's own store.
    """
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    argv = [sys.executable, str(RESOLVER_SRC), "--durable-root", str(root)]
    for pattern in search_globs or []:
        argv += ["--search-glob", str(pattern)]
    argv += ["--node", node, "--timeout-sec", str(timeout_sec)]
    return subprocess.run(argv, capture_output=True, text=True, timeout=60, env=env)


def chosen_path(proc):
    assert proc.returncode == 0, f"resolver failed: {proc.stderr}"
    return json.loads(proc.stdout)["companion_path"]


def test_running_profile_wins_over_a_newer_foreign_version(tmp_path):
    """The active profile's 1.0.6 beats a foreign 1.0.10. Newest-semver decides
    WITHIN a tier, never across them -- an operator who pins an older companion
    in the profile they are running in has made a decision, and a foreign
    profile's contents are not one."""
    home = tmp_path / "home"
    make_companion(home, "1.0.6", ".claudeA")
    make_companion(home, "1.0.10", ".claudeB")
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    got = chosen_path(run_resolver_env(tmp_path, node, home, home / ".claudeA"))
    assert "/.claudeA/" in got and "/1.0.6/" in got, got


@pytest.mark.parametrize("active,foreign", [(".claude2", ".claude9"), (".claude9", ".claude2")])
def test_running_profile_wins_an_equal_version_lexical_tie(tmp_path, active, foreign):
    """THE LIVE REPRODUCTION. #287 measured four 1.0.6 copies tying on version,
    with `.claude3` winning purely on lexical path order while the session ran
    under `.claude2`. Both orientations are driven because ONE of them is also
    satisfied by "on a tie take the lexically smaller path", which implements no
    profile ownership at all."""
    home = tmp_path / "home"
    make_companion(home, "1.0.6", active)
    make_companion(home, "1.0.6", foreign)
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    got = chosen_path(run_resolver_env(tmp_path, node, home, home / active))
    assert f"/{active}/" in got, got


def test_unset_config_dir_prefers_home_dot_claude(tmp_path):
    """CLAUDE_CONFIG_DIR absent is the CLI's own fallback case: `~/.claude`.
    Pinned against a foreign `.claude3` holding a NEWER companion."""
    home = tmp_path / "home"
    make_companion(home, "1.0.6", ".claude")
    make_companion(home, "1.0.10", ".claude3")
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    got = chosen_path(run_resolver_env(tmp_path, node, home, None))
    assert "/.claude/" in got and "/1.0.6/" in got, got


def test_falls_back_cross_profile_when_running_profile_has_none(tmp_path):
    """A foreign companion is a FALLBACK -- available, not preferred."""
    home = tmp_path / "home"
    (home / ".claudeEmpty" / "plugins").mkdir(parents=True)
    make_companion(home, "1.0.6", ".claudeA")
    make_companion(home, "1.0.10", ".claudeB")
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    got = chosen_path(run_resolver_env(tmp_path, node, home, home / ".claudeEmpty"))
    assert "/.claudeB/" in got and "/1.0.10/" in got, got


@pytest.mark.parametrize("config_dir", ["", "   ", "relative/profile", "$NOPE/.claude"])
def test_unusable_config_dir_values_abort_without_binding(tmp_path, config_dir):
    """A present-but-empty, whitespace, relative, or unexpanded value is not an
    absolute directory, so the RUNNING profile cannot be identified -- and that
    is a validation failure, not an empty tier. Skipping to the cross-profile
    tier here would silently bind a FOREIGN profile's companion, which is the
    #287 defect itself; globbing the value against THIS script's cwd would bind
    a directory the CLI never resolved. So it aborts, names the variable, and
    prints nothing on stdout for a caller to consume."""
    home = tmp_path / "home"
    make_companion(home, "1.0.10", ".claudeB")
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    proc = run_resolver_env(tmp_path, node, home, config_dir)
    assert proc.returncode != 0, f"expected an abort, got: {proc.stdout}"
    assert proc.stdout.strip() == "", proc.stdout
    assert "CLAUDE_CONFIG_DIR" in proc.stderr, proc.stderr


def test_nonexistent_config_dir_falls_back(tmp_path):
    home = tmp_path / "home"
    make_companion(home, "1.0.10", ".claudeB")
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    got = chosen_path(run_resolver_env(tmp_path, node, home, home / ".claudeGone"))
    assert "/.claudeB/" in got, got


@pytest.mark.parametrize("active,decoy", [
    (".claude[1]", ".claude1"),     # bracket set
    (".claudeX*", ".claudeXY"),     # star
    (".claude?", ".claudeZ"),       # single-char wildcard
])
def test_glob_metacharacters_in_config_dir_are_literal(tmp_path, active, decoy):
    """CLAUDE_CONFIG_DIR is an operator-authored PATH, not a pattern. All three
    characters are legal in macOS filenames, and unescaped each one makes the
    profile tier match a DIFFERENT profile's store -- the exact green
    wrong-binary binding #287 is about. The bracket case alone is passed by a
    hand-rolled `replace("[", "[[]")` that leaves `*` and `?` live, which is why
    all three are driven."""
    home = tmp_path / "home"
    make_companion(home, "1.0.6", active)
    make_companion(home, "1.0.10", decoy)
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    got = chosen_path(run_resolver_env(tmp_path, node, home, home / active))
    assert f"/{active}/" in got and "/1.0.6/" in got, got


def test_broken_active_companion_aborts_instead_of_falling_back(tmp_path):
    """A tier falls through ONLY on zero candidates, never on a validation
    failure. An implementation that caught the active profile's failing `status`
    and quietly retried the cross-profile tier would return 0 with a FOREIGN
    binary -- the defect under repair, one step later. Every other case here uses
    a node stub that succeeds on any invocation and cannot tell the two
    implementations apart; this stub fails only for the active profile's path,
    so a fallback would visibly succeed."""
    home = tmp_path / "home"
    make_companion(home, "1.0.6", ".claudeBroken")
    make_companion(home, "1.0.10", ".claudeOk")
    node = write_fake_node(
        tmp_path,
        "import sys\nsys.exit(1 if '.claudeBroken' in sys.argv[1] else 0)\n",
    )
    proc = run_resolver_env(tmp_path, node, home, home / ".claudeBroken")
    assert proc.returncode != 0, f"expected an abort, got: {proc.stdout}"
    assert proc.stdout.strip() == "", proc.stdout


def test_unsafe_active_companion_aborts_instead_of_falling_back(tmp_path):
    """The SECOND abort branch, and it needs its own case. The status-failure
    test above leaves the unsafe-path branch free to be turned into a `continue`
    with the whole suite still green: the pre-existing unsafe-path test drives a
    single override tier, so it would keep exiting nonzero for want of anywhere
    to fall through TO. Here the active profile's path is unsafe (a single quote
    -- one of the two carriers `path_is_safe` refuses) while a foreign profile
    holds a perfectly good companion, so a fallthrough would visibly succeed."""
    home = tmp_path / "home"
    make_companion(home, "1.0.6", ".claude'quote")
    make_companion(home, "1.0.10", ".claudeOk")
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    proc = run_resolver_env(tmp_path, node, home, home / ".claude'quote")
    assert proc.returncode != 0, f"expected an abort, got: {proc.stdout}"
    assert proc.stdout.strip() == "", proc.stdout
    assert "single quote" in proc.stderr, proc.stderr


def test_an_empty_override_does_not_leak_into_the_default_tiers(tmp_path):
    """`--search-glob` is authoritative even when it matches NOTHING. The
    override test below always hands it a real candidate, so an implementation
    that consults the defaults "only when the override is empty" passes it -- and
    that implementation binds a profile the operator explicitly searched past.
    The pre-existing zero-candidate test cannot see this either: it inherits the
    host environment, so on a machine with no profile store it exits nonzero for
    the wrong reason."""
    home = tmp_path / "home"
    make_companion(home, "1.0.10", ".claudeA")          # would win a default tier
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    proc = run_resolver_env(
        tmp_path, node, home, home / ".claudeA",
        search_globs=[tmp_path / "nothing-here" / "**" / "codex-companion.mjs"],
    )
    assert proc.returncode != 0, f"the override leaked into the defaults: {proc.stdout}"
    assert "claudeA" not in proc.stdout, proc.stdout


def test_repeated_search_globs_stay_one_union_tier(tmp_path):
    """Repeated --search-glob values are a UNION resolved by newest-semver, not a
    priority order -- `action="append"` has always built one flat list. Both
    orderings are driven: a reverse-priority implementation passes whichever
    single fixture happens to put the newer companion in the losing position."""
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    older = tmp_path / "older"
    newer = tmp_path / "newer"
    make_companion(older, "1.0.6", "profO")
    make_companion(newer, "1.0.10", "profN")
    def pat(base):
        return str(base / "*" / "plugins" / "cache" / "openai-codex" / "**"
                   / "codex-companion.mjs")

    for first, second in [(older, newer), (newer, older)]:
        proc = run_resolver_env(
            tmp_path, node, tmp_path / "no-such-home", None,
            search_globs=[pat(first), pat(second)],
        )
        assert "/1.0.10/" in chosen_path(proc), (first, proc.stdout)


def test_search_glob_override_ignores_the_profile_tier(tmp_path):
    """The override is fully authoritative. This is the property the REST of this
    file depends on for isolation: every pre-#287 test passes --search-glob so
    the real ~/.claude* store is never touched, and a profile preference that
    leaked past the override would bind this machine's own store instead."""
    home = tmp_path / "home"
    make_companion(home, "1.0.10", ".claudeA")          # would win any default tier
    elsewhere = tmp_path / "elsewhere"
    make_companion(elsewhere, "1.0.6", "profX")
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    proc = run_resolver_env(
        tmp_path, node, home, home / ".claudeA",
        search_globs=[elsewhere / "*" / "plugins" / "cache" / "openai-codex" / "**"
                      / "codex-companion.mjs"],
    )
    got = chosen_path(proc)
    assert "/profX/" in got and "/1.0.6/" in got, got


# --------------------------------------------------------------------------- #
# white-box: running_profile_dir() mirrors the CLI and normalizes NOTHING
# --------------------------------------------------------------------------- #
def test_running_profile_dir_absent_falls_back_to_home_dot_claude(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert rcc.running_profile_dir() == (str(tmp_path / ".claude"), None)


@pytest.mark.parametrize("raw", ["/tmp/.claude ", "/tmp/.claude2/", "/tmp/a b/.claude"])
def test_running_profile_dir_uses_a_present_value_verbatim(monkeypatch, raw):
    """No strip, no normalization. The CLI uses the value as given, and a
    directory whose name really does end in a space is a directory this must
    still find -- trimming it names a DIFFERENT one."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", raw)
    assert rcc.running_profile_dir() == (raw, None)


@pytest.mark.parametrize("raw", ["", "   ", "relative/dir", "~/.claude2"])
def test_running_profile_dir_rejects_a_non_absolute_value(monkeypatch, raw):
    """Fail-closed: a REASON, never a silently skipped tier. `~/.claude2` is in
    this list deliberately -- the CLI does not expand a tilde either, so a quoted
    literal is not a profile directory. The reason has to name the variable, since
    the operator's only fix is to change or unset it."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", raw)
    got, problem = rcc.running_profile_dir()
    assert got is None
    assert problem and "CLAUDE_CONFIG_DIR" in problem, problem


def test_default_glob_tiers_reports_the_problem_instead_of_dropping_the_tier(monkeypatch):
    """The regression the abort exists to stop: returning just the cross-profile
    tier here reads as an ordinary one-tier resolution downstream, and resolve()
    would then bind a FOREIGN profile's companion with exit 0."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/dir")
    tiers, problem = rcc.default_glob_tiers()
    assert tiers is None, tiers
    assert problem and "CLAUDE_CONFIG_DIR" in problem, problem


# Kept at the TRUE END of the file. Mid-file it exits before every definition
# below it, so `python tests/resolve_codex_companion.test.py` reported a green
# run over only the tests that happened to be defined ABOVE it -- a false green
# that reads exactly like a full pass.
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
