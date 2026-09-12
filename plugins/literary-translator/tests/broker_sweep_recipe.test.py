"""#915: the operator-facing broker-sweep recipe documented in
`skills/literary-translator/references/gotchas.md`'s "Orphaned codex app-server
brokers" section must actually do what it claims -- match only one durable root's own
brokers, by the tag anchored right after the family marker on the LAST path component,
and never match a foreign root's live broker through a label collision or through an
ancestor directory.

These tests lift the shell fence out of gotchas.md itself and run THAT text, never a
retyped copy: a doc rewrap that silently breaks the recipe (widens the pattern, drops
the anchor, reorders the tag) must fail here, not ship unnoticed. As in
`sandbox_broker_teardown.test.py`, the positive/negative cases drive real processes
rather than a mocked signal -- the thing that can silently stop working is the
pattern's ability to match (or refuse to match) a real command line, and a mock proves
nothing about that.
"""

import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
GOTCHAS = PLUGIN_ROOT / "skills" / "literary-translator" / "references" / "gotchas.md"
assert GOTCHAS.is_file(), f"expected the doc at {GOTCHAS}"

skip_no_pgrep = pytest.mark.skipif(
    shutil.which("pgrep") is None or shutil.which("pkill") is None,
    reason="pgrep/pkill unavailable",
)

DECOY_LIFETIME_SEC = 60
REAP_TIMEOUT_SEC = 15
SURVIVE_WATCH_SEC = 1.5
DECOY_MARKER = "cxc-lt915-sweep-decoy"


def _extract_fences():
    """Pulls BOTH shell fences out of gotchas.md's orphaned-broker section, verbatim:
    the LOOK fence (which must kill nothing) and the SWEEP fence (the `pkill`).

    Fails loudly rather than finding nothing -- a test that silently matched zero
    fences would pass every negative case for free by running an empty script. Each
    fence is additionally checked for the tokens that make it the command it claims to
    be, so a doc edit that drops one is a test failure rather than a quiet no-op.
    """
    text = GOTCHAS.read_text(encoding="utf-8")
    assert text.count("## 16.") == 1, (
        "expected exactly one '## 16.' heading in gotchas.md, found %d"
        % text.count("## 16.")
    )
    section = text[text.index("## 16."):]
    fences = re.findall(r"```sh\n(.*?)\n```", section, re.DOTALL)
    assert len(fences) == 2, (
        "expected exactly two ```sh fences under gotchas.md's '## 16.' section (LOOK "
        "then SWEEP), found %d" % len(fences)
    )
    look, sweep = fences
    for needed in ("DURABLE_ROOT:?", "proj8=", "pat=", "pgrep -f ", "ps -ww -o pid=,args="):
        assert needed in look, "LOOK fence is missing %r:\n%s" % (needed, look)
    assert "pkill" not in look, (
        "the LOOK fence must not be able to kill anything:\n%s" % look
    )
    # pgrep's long form is not portable in the way this preview depends on: BSD's
    # `-fl` prints the whole command line, procps' `-l` prints only the process NAME.
    # The behavioural test below catches that on Linux (which is what CI runs), but it
    # would pass on a BSD/macOS developer machine, so pin the construct here too.
    for banned in ("pgrep -fl", "pgrep -lf", "pgrep -l "):
        assert banned not in look, (
            "the LOOK fence must print argv portably via `ps -o pid=,args=`, not %r, "
            "which prints only the process name on Linux/procps:\n%s" % (banned, look)
        )
    for needed in ("pkill -TERM -f", '"$pat"'):
        assert needed in sweep, "SWEEP fence is missing %r:\n%s" % (needed, sweep)
    return look, sweep


LOOK_FENCE, PKILL_FENCE = _extract_fences()
# The operator runs the SWEEP fence in the same shell as the LOOK fence, so $pat is
# still the pattern they just reviewed. Tests reproduce that by running the two
# together; nothing here retypes the pattern.
SWEEP_FENCE = LOOK_FENCE + "\n" + PKILL_FENCE


def _expected_proj8(root):
    """Independent oracle, used ONLY by the identity tests below to check the fence's
    own `proj8=` line against the spec in plan section 4.2. Every positive/negative
    sweep test runs the fence's line directly instead, never this function -- if the
    doc's line itself drifted from this formula, the sweep tests must not paper over
    that by re-deriving the "right" answer the same wrong way."""
    return hashlib.sha256(os.path.realpath(str(root)).encode()).hexdigest()[:8]


def _spawn_decoy(cwd_arg, *, script_name="app-server-broker.mjs", pid_file_arg=None):
    """A process whose command line has the same argv shape as a real broker, with
    `cwd_arg` in the `--cwd` slot. `script_name` is a knob for the negative case where
    the path matches but the process is not a broker; `pid_file_arg` is a knob for the
    negative case where a TAG-shaped string sits in a LATER argument than `--cwd`."""
    pid_file = pid_file_arg if pid_file_arg is not None else (
        "/tmp/%s/broker.pid" % DECOY_MARKER)
    argv = [
        sys.executable, "-c",
        "import sys, time; time.sleep(%d)" % DECOY_LIFETIME_SEC,
        "/plugins/cache/openai-codex/codex/1.0.6/scripts/%s" % script_name,
        "serve", "--endpoint", "unix:/tmp/%s/broker.sock" % DECOY_MARKER,
        "--cwd", str(cwd_arg),
        "--pid-file", str(pid_file),
    ]
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def decoys():
    """Spawns decoys and guarantees every one is gone when the test ends, whatever it
    asserted -- a leaked 60 s sleeper would otherwise be matched by a LATER test's
    pgrep, or by the actual operator sweep on this machine."""
    spawned = []

    def factory(cwd_arg, **kw):
        proc = _spawn_decoy(cwd_arg, **kw)
        spawned.append(proc)
        _await_visible_to_pgrep(proc)
        return proc

    yield factory
    for proc in spawned:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=REAP_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:  # pragma: no cover - a killed process reaps
            pass


def _await_visible_to_pgrep(proc):
    """`Popen` returns before the kernel has necessarily published the new argv to
    /proc (or BSD's kinfo). Without this, a positive test could pass or fail on
    scheduling rather than on the pattern, and a negative test would pass vacuously
    because the decoy was not yet visible to match."""
    deadline = time.monotonic() + REAP_TIMEOUT_SEC
    while time.monotonic() < deadline:
        found = subprocess.run(["pgrep", "-f", DECOY_MARKER],
                               capture_output=True, text=True, timeout=10)
        if str(proc.pid) in (found.stdout or "").split():
            return
        time.sleep(0.05)
    raise AssertionError("decoy pid %d never became visible to pgrep" % proc.pid)


def _assert_terminated(proc):
    proc.wait(timeout=REAP_TIMEOUT_SEC)
    assert proc.returncode == -signal.SIGTERM, (
        "expected SIGTERM, got returncode %r" % (proc.returncode,))


def _assert_survives(proc):
    with pytest.raises(subprocess.TimeoutExpired):
        proc.wait(timeout=SURVIVE_WATCH_SEC)


def _run_sweep(durable_root):
    """Runs the documented fence verbatim against a real DURABLE_ROOT. Never
    reimplements proj8 or the pattern in Python -- if the doc's own text is wrong,
    this must fail the same way an operator's copy-paste would."""
    env = dict(os.environ)
    env["DURABLE_ROOT"] = str(durable_root)
    return subprocess.run(
        ["bash", "-c", SWEEP_FENCE],
        env=env, capture_output=True, text=True, timeout=REAP_TIMEOUT_SEC,
    )


def _run_look(durable_root):
    """Runs the LOOK fence alone, verbatim. It must never kill anything, so every test
    that uses it also asserts the decoy is still alive afterwards."""
    env = dict(os.environ)
    env["DURABLE_ROOT"] = str(durable_root)
    return subprocess.run(
        ["bash", "-c", LOOK_FENCE],
        env=env, capture_output=True, text=True, timeout=REAP_TIMEOUT_SEC,
    )


# --------------------------------------------------------------------------- #
# proj8 identity -- the fence's own line, not a reimplementation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("spelling", ["bare", "trailing_slash", "symlink"])
def test_proj8_identity_across_root_spellings(tmp_path, spelling):
    """The fence's own `proj8=` line, run against three spellings of one root, must
    agree with each other and with the independent oracle -- a symlink or a trailing
    slash must never change which brokers a sweep matches."""
    real_root = tmp_path / "book"
    real_root.mkdir()
    if spelling == "bare":
        root_arg = real_root
    elif spelling == "trailing_slash":
        root_arg = Path(str(real_root) + "/")
    else:
        root_arg = tmp_path / "book_link"
        root_arg.symlink_to(real_root)

    proj8_lines = [line for line in SWEEP_FENCE.splitlines() if line.startswith("proj8=")]
    assert len(proj8_lines) == 1, (SWEEP_FENCE, proj8_lines)
    proj8_line = proj8_lines[0]

    env = dict(os.environ)
    env["DURABLE_ROOT"] = str(root_arg)
    result = subprocess.run(
        ["bash", "-c", proj8_line + '\necho "$proj8"'],
        env=env, capture_output=True, text=True, timeout=REAP_TIMEOUT_SEC,
    )
    assert result.returncode == 0, result.stderr
    got = result.stdout.strip()
    assert got == _expected_proj8(real_root), (root_arg, got, result.stderr)


# --------------------------------------------------------------------------- #
# the sweep, run end to end against real decoys
# --------------------------------------------------------------------------- #

@skip_no_pgrep
@pytest.mark.parametrize("family", ["ltcj", "ltnd", "ltgd"])
def test_sweep_kills_a_matching_broker(tmp_path, decoys, family):
    """A decoy shaped exactly like a real broker for the target root, tagged right
    after its own family marker, is matched and killed by SIGTERM."""
    target_root = tmp_path / "target_book"
    target_root.mkdir()
    proj8 = _expected_proj8(target_root)
    cwd = tmp_path / "segments" / ("%s.p%s.seg01.abcdef01" % (family, proj8))
    proc = decoys(cwd)

    result = _run_sweep(target_root)
    assert result.returncode in (0, 1), result.stderr  # pkill: 0 killed, 1 no match
    _assert_terminated(proc)


@skip_no_pgrep
def test_look_fence_prints_the_argv_it_would_kill_and_kills_nothing(tmp_path, decoys):
    """The LOOK fence has to show the operator the thing they are deciding on.

    Three review rounds passed over this recipe while the look step was never asserted
    on its OUTPUT, only on its exit status -- and an exit-0 command that prints nothing
    distinguishing looks exactly like one that works. The doc advertises this step as
    the mitigation for a pattern that is deliberately not bound to `--cwd`, so if it
    does not print the `--cwd` value the mitigation does not exist.

    It printed only `python3` / `node` per hit while the fence used `pgrep -l`, whose
    long form prints the process NAME on Linux/procps. `ps -o pid=,args=` prints the
    argv on both platforms; this test is what holds that.
    """
    target_root = tmp_path / "target_book"
    target_root.mkdir()
    proj8 = _expected_proj8(target_root)
    cwd = tmp_path / "segments" / ("ltcj.p%s.seg01.abcdef01" % proj8)
    proc = decoys(cwd)

    result = _run_look(target_root)
    assert result.returncode == 0, result.stderr
    assert str(cwd) in result.stdout, (
        "the LOOK fence must print the --cwd it would kill, not just a process name;\n"
        "stdout was:\n%s" % result.stdout
    )
    assert "app-server-broker" in result.stdout, result.stdout
    assert str(proc.pid) in result.stdout, result.stdout
    # And it is a preview: nothing may die.
    _assert_survives(proc)


@skip_no_pgrep
def test_look_fence_is_not_truncated_by_a_narrow_terminal(tmp_path, decoys):
    """The preview is meant to be pasted into an interactive shell, so it must survive
    a narrow terminal.

    `ps` truncates `args` to the output width -- to the terminal's columns when stdout
    is a TTY, and to 80 columns on procps even through a pipe -- and what gets cut is
    the END of the line: the `app-server-broker.mjs` and the `--cwd` the operator is
    supposed to be checking. `-ww` forces unlimited width on both procps and BSD.

    This test drives the fence through a real 80-column pseudo-terminal rather than a
    pipe. The pipe version of this assertion passed on a Mac while the interactive case
    was broken, which is the whole reason it exists: a captured pipe is not the
    environment the doc tells the operator to use.
    """
    import fcntl
    import pty
    import struct
    import termios

    target_root = tmp_path / "target_book"
    target_root.mkdir()
    proj8 = _expected_proj8(target_root)
    # A deliberately long parent, so a truncated line cannot reach the tail by luck.
    deep = tmp_path / ("padding_" + "d" * 60) / ("padding_" + "e" * 60)
    deep.mkdir(parents=True)
    cwd = deep / ("ltcj.p%s.seg01.abcdef01" % proj8)
    proc = decoys(cwd)

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    env = dict(os.environ)
    env["DURABLE_ROOT"] = str(target_root)
    env["COLUMNS"] = "80"
    try:
        child = subprocess.Popen(
            ["bash", "-c", LOOK_FENCE],
            stdout=slave, stderr=slave, stdin=slave, env=env, close_fds=True,
        )
        os.close(slave)
        chunks = []
        while True:
            try:
                data = os.read(master, 4096)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
        child.wait(timeout=REAP_TIMEOUT_SEC)
    finally:
        os.close(master)
    out = b"".join(chunks).decode("utf-8", "replace")

    assert out.strip(), "the LOOK fence printed nothing through a pty"
    assert "app-server-broker" in out, (
        "a narrow terminal truncated the preview before the script name:\n%s" % out
    )
    assert str(cwd) in out.replace("\r\n", "\n"), (
        "a narrow terminal truncated the preview before the --cwd, which is the one "
        "thing the operator has to read:\n%s" % out
    )
    _assert_survives(proc)


@skip_no_pgrep
def test_look_fence_says_plainly_when_nothing_carries_the_tag(tmp_path, decoys):
    """An empty preview must read as an answer, not as a command that did nothing.

    A bare `pgrep` prints nothing and exits 1 when it matches nothing, which on the one
    command meant to be run after a crash is indistinguishable from a recipe that is
    broken. The fence says so in words instead, and still exits 0.
    """
    target_root = tmp_path / "target_book"
    target_root.mkdir()
    other_root = tmp_path / "other_book"
    other_root.mkdir()
    # A live broker that belongs to a DIFFERENT root, so the corpus is not empty.
    cwd = tmp_path / "segments" / ("ltcj.p%s.seg01.abcdef01" % _expected_proj8(other_root))
    proc = decoys(cwd)

    result = _run_look(target_root)
    assert result.returncode == 0, result.stderr
    assert "no orphaned broker carries the tag" in result.stdout, result.stdout
    assert str(cwd) not in result.stdout, result.stdout
    _assert_survives(proc)


@skip_no_pgrep
@pytest.mark.parametrize("parent_name", ["My Tmp Dir", "My -Dir", "build --out"])
def test_sweep_kills_a_broker_under_an_awkwardly_named_parent(tmp_path, decoys, parent_name):
    """No spelling of the PARENT directory may hide a sandbox from the sweep.

    A space alone was the first reported miss; a space followed by a hyphen was the
    second, because the pattern that fixed the first treated " -" as the start of the
    next argument. Both were false all-clears. These names are the shapes that broke the
    two earlier patterns, pinned so a future tightening cannot quietly reintroduce
    either.
    """
    target_root = tmp_path / "target_book"
    target_root.mkdir()
    proj8 = _expected_proj8(target_root)
    parent = tmp_path / parent_name
    parent.mkdir()
    cwd = parent / ("ltnd.p%s.unit_7.abcdef01" % proj8)
    proc = decoys(cwd)

    result = _run_sweep(target_root)
    assert result.returncode in (0, 1), result.stderr
    _assert_terminated(proc)


@skip_no_pgrep
def test_sweep_spares_a_label_collision(tmp_path, decoys):
    """A foreign sandbox whose free-form LABEL happens to spell out the target root's
    proj8 -- but whose own tag is a different digest -- must not be swept. The tag is
    anchored to the family marker precisely so a label can never forge it."""
    target_root = tmp_path / "target_book"
    target_root.mkdir()
    proj8 = _expected_proj8(target_root)
    # The tag here (pDEADBEEF) is NOT this root's proj8; the label that follows it
    # merely happens to equal the target's proj8 string.
    cwd = tmp_path / "segments" / ("ltcj.pDEADBEEF.%s.x.y" % proj8)
    proc = decoys(cwd)

    _run_sweep(target_root)
    _assert_survives(proc)


@skip_no_pgrep
def test_sweep_spares_an_ancestor_directory_collision(tmp_path, decoys):
    """A foreign sandbox whose PARENT directory carries the target's tag, but whose
    own last path component does not, must not be swept -- otherwise a TMPDIR nested
    inside an old tagged sandbox would let this root's sweep reach through the
    ancestor and kill a different root's live broker."""
    target_root = tmp_path / "target_book"
    target_root.mkdir()
    proj8 = _expected_proj8(target_root)
    tagged_parent = tmp_path / ("ltcj.p%s.foo" % proj8)
    cwd = tagged_parent / "unrelated_leaf"
    proc = decoys(cwd)

    _run_sweep(target_root)
    _assert_survives(proc)


@skip_no_pgrep
def test_sweep_spares_a_script_name_suffix_collision(tmp_path, decoys):
    """A process whose --cwd carries a correctly tagged path, and whose script name
    ends in `app-server-broker.mjs` as a bare SUFFIX (`not-app-server-broker.mjs`),
    must not be swept -- the pattern requires a space or path separator immediately
    before the broker's script name, not merely that substring anywhere in the
    command line. Without that boundary this decoy's name would forge a match."""
    target_root = tmp_path / "target_book"
    target_root.mkdir()
    proj8 = _expected_proj8(target_root)
    cwd = tmp_path / "segments" / ("ltcj.p%s.seg01.abcdef01" % proj8)
    proc = decoys(cwd, script_name="not-app-server-broker.mjs")

    _run_sweep(target_root)
    _assert_survives(proc)


@skip_no_pgrep
def test_sweep_matches_a_tag_in_a_later_argument_and_that_is_ACCEPTED(tmp_path, decoys):
    """The recipe does NOT bind the tag to the --cwd value, and this test pins that as
    the DELIBERATE state rather than leaving it undocumented.

    Two earlier patterns tried to bind it and both produced a false all-clear on real
    paths -- `[^ ]*` skipped any sandbox under a TMPDIR containing a space, and
    `([^ ]| [^-])*` then skipped one containing a space followed by a hyphen. The cause
    is structural: `pgrep -f` matches a FLATTENED command line, in which a space is both
    the argument separator and an ordinary path character, so no regular expression can
    tell the two apart. Missing one of the operator's own orphans is strictly worse than
    the hypothetical this bought, so the binding was dropped.

    What remains is the residual recorded in gotchas.md: were the companion ever changed
    to pass a SECOND sandbox-derived path in one command line, a sweep for one root could
    match a broker whose --cwd belongs to another. Today it passes exactly one such path,
    so the shape below cannot occur in a real broker's argv -- this decoy has to be
    constructed. The recipe's `pgrep -fl` look-first step is the mitigation, and this
    assertion exists so that anyone who re-tightens the pattern later sees this case flip
    and has to read the reasoning first.
    """
    target_root = tmp_path / "target_book"
    target_root.mkdir()
    target_proj8 = _expected_proj8(target_root)
    other_root = tmp_path / "other_book"
    other_root.mkdir()
    other_proj8 = _expected_proj8(other_root)

    cwd = tmp_path / "segments" / ("ltcj.p%s.seg01.aaaaaaaa" % other_proj8)
    pid_file = "/tmp/%s/ltcj.p%s.seg01.bbbbbbbb" % (DECOY_MARKER, target_proj8)
    proc = decoys(cwd, pid_file_arg=pid_file)

    result = _run_sweep(target_root)
    assert result.returncode in (0, 1), result.stderr
    _assert_terminated(proc)


def test_fence_refuses_to_run_without_durable_root():
    """A crash-recovery command that silently computes a plausible-looking answer when
    misconfigured is worse than one that visibly fails: with DURABLE_ROOT unset,
    `os.path.realpath('')` would resolve to the caller's own cwd and hand back an
    empty, falsely reassuring `pgrep -fl` instead of an error. Confirm the fence's own
    guard line refuses to run rather than doing that."""
    env = dict(os.environ)
    env.pop("DURABLE_ROOT", None)
    result = subprocess.run(
        ["bash", "-c", SWEEP_FENCE],
        env=env, capture_output=True, text=True, timeout=REAP_TIMEOUT_SEC,
    )
    assert result.returncode != 0, (result.returncode, result.stdout, result.stderr)
    assert "DURABLE_ROOT" in result.stderr, result.stderr
