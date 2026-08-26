#!/usr/bin/env python3
"""Put `code-limit` on PATH: a three-line shim that runs this plugin's shipped usage-limit report.

The command must not be a second copy of the report. What gets installed is a shell file whose
only executable statement is `exec python3 <report_limits.py> "$@"`, so there is exactly one
implementation and the shim cannot drift from it. `exec` also means the report's own exit status
IS the command's, and that stdin, stdout and stderr pass straight through.

Two things this installer refuses rather than guesses at.

A VERSION-SCOPED CACHE as the source. Claude Code stores plugins at
`<CONFIG_DIR>/plugins/cache/<marketplace>/<plugin>/<version>/`, and startup GC deletes any version
directory the acting profile's catalogue no longer references -- see the sibling
`multi-profile-plugins` skill's `references/cli-mechanism.md`. A path baked out of there names one
version, so after an update the command keeps running the old report while that directory survives
and breaks once it is collected. Neither failure announces itself, so the source is refused up
front and the user is told to install from a version-stable copy instead.

A FOREIGN FILE at a managed name. `code-limit`, `claude-limit` and `claude-limits` are names a user
may already own -- on the machine this was written for, all three existed as someone else's script.
An entry is treated as ours only when it is a regular file whose whole content is exactly the shim
this installer generates; everything else, symlinks included, is left alone unless `--force` says
otherwise.

The interpreter is NOT baked. `python3` is resolved from PATH at run time, exactly as the shim's
own shebang and this skill's documented `python3 scripts/report_limits.py` invocation do -- every
absolute interpreter path available at install time (a Homebrew Cellar path carrying a patch
version, a virtualenv that gets deleted) is less durable than the command is meant to be. The
report needs Python 3.11+; what this installer prints about `python3` is a disclosure of what it
found, not a gate it could honestly enforce over a shell it does not control.

Python 3.11+, standard library only. Ships mode 644 -- run it through `python3`.
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPORT_NAME = "report_limits.py"
PRIMARY = "code-limit"
# Managed only where they ALREADY exist. Creating them for a user who never had them would be
# manufacturing the legacy names this change exists to retire.
LEGACY = ("claude-limit", "claude-limits")
MARKER = "# multi-profile-plugins code-limits shim -- regenerate with install_code_limit.py"

EXEC_PREFIX = "exec python3 "
EXEC_SUFFIX = ' "$@"'

# Emitted when the source is a version-scoped plugin cache. The path gets a line of its own: it is
# the one part of this a user has to read character by character.
VERSION_CACHE_REFUSAL = """\
error: {report}
  is inside a version-scoped plugin cache. Those directories name ONE version and are
  garbage-collected, so a command installed from here silently keeps running an old report and
  then breaks when it is removed. Run this installer from a version-stable copy instead -- the
  marketplace checkout,
  ~/.claude/plugins/marketplaces/<marketplace>/.../skills/code-limits/scripts/,
  or a clone of the repo."""


def posix_quote(text: str) -> str:
    """Single-quote for `sh`, ALWAYS -- one shape, whatever the path contains.

    `shlex.quote` leaves an ordinary path bare, which would make the shim's third line have two
    possible forms and a pin on it correspondingly weaker. A closing quote, a literal quote and a
    reopening quote is the POSIX way to carry a quote through single quotes.
    """
    return "'" + text.replace("'", "'\"'\"'") + "'"


def emittable(target: str) -> bool:
    """Whether this installer could ever have written `target` into a shim.

    Emission and classification have to accept the SAME language, and this is the one predicate
    both use. `main` refuses a report path carrying a control character rather than escaping it
    into a shell file; a decoder that accepts one anyway lets a file this installer could never
    have produced count as its own -- and a planted file at a managed name is exactly the
    untrusted surface the ownership test exists for. An empty target is impossible for the same
    reason.
    """
    return target != "" and not any(ord(character) < 32 or 0xD800 <= ord(character) <= 0xDFFF
                                    for character in target)


def unquote_canonical(text: str) -> str | None:
    """Reverse `posix_quote`, and ONLY for output `posix_quote` could have produced.

    This is where an "is it one of ours" test has to be exact rather than plausible. A pattern
    like `'.+'` accepts `'x' ; echo mine # '` -- a quoted region, a second command, and a comment
    that swallows the closing quote -- so an unrelated executable planted at a managed name is
    adopted and rewritten with no confirmation. Canonical output has one property that string
    cannot fake: outside the `'"'"'` escape, the interior holds no quote at all.
    """
    if len(text) < 2 or not text.startswith("'") or not text.endswith("'"):
        return None
    inner = text[1:-1]
    if "'" in inner.replace("'\"'\"'", "\x00"):
        return None
    return inner.replace("'\"'\"'", "'")


def shim_target(content: bytes) -> str | None:
    """The path a shim execs, or None when `content` is not a shim this installer wrote.

    Takes BYTES. `Path.read_text` translates newlines, so a CRLF-mangled copy of a genuine shim
    compares equal to the real thing while its `#!/bin/sh\r` line makes the command exit 127 --
    an installer reporting success over a command that cannot run.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = text.split("\n")
    # Exactly three lines and a trailing newline: `split` yields a final empty element for the
    # trailing newline, and any fourth line of somebody else's making shows up here.
    if len(lines) != 4 or lines[3] != "" or lines[0] != "#!/bin/sh" or lines[1] != MARKER:
        return None
    body = lines[2]
    if not body.startswith(EXEC_PREFIX) or not body.endswith(EXEC_SUFFIX):
        return None
    target = unquote_canonical(body[len(EXEC_PREFIX):-len(EXEC_SUFFIX)])
    return target if target is not None and emittable(target) else None


def shim_text(report: Path) -> str:
    """The whole shim. One statement, and the report's path is the only thing interpolated.

    Built from the SAME prefix and suffix `shim_target` decomposes, so the emitter and the
    ownership test cannot drift into disagreeing about the shape of the one line that matters.
    """
    return f"#!/bin/sh\n{MARKER}\n{EXEC_PREFIX}{posix_quote(str(report))}{EXEC_SUFFIX}\n"


def in_version_cache(report: Path) -> bool:
    """True for `<...>/plugins/cache/<marketplace>/<plugin>/<version>/skills/code-limits/scripts/`.

    Structural, against the layout the sibling skill documents -- not a substring of the path,
    which would also match a checkout that happens to live under a directory called `cache`.
    The length test is load-bearing: `Path.parents` raises IndexError past its end, and a valid
    shallow source such as `/tmp/x/code-limits/scripts/report_limits.py` has only six parents.
    """
    parents = report.parents
    return (len(parents) >= 8
            and parents[6].name == "cache"
            and parents[7].name == "plugins")


def is_ours(path: Path) -> bool:
    """Whether this installer wrote `path`.

    lstat FIRST: a symlink is never ours, whatever it points at -- following one would read a
    genuine shim through a link the user placed by hand and then replace that link without asking.
    """
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    try:
        content = path.read_bytes()
    except OSError:
        return False
    return shim_target(content) is not None


def discard_staging(path: str) -> None:
    """Remove a staging file, never raising -- tidying it is not the job the caller was given.

    On the failure path an exception is already on its way up and must not be replaced by this
    one; on the success path the install has already happened and a leftover dot-file must not be
    reported as a failure.
    """
    try:
        os.unlink(path)
    except OSError:
        pass


def write_atomically(dest: Path, data: bytes, *, clobber: bool) -> None:
    """Stage the shim beside `dest`, then put it in place in one step.

    Deliberately NO unlink first. Both `rename(2)` and `link(2)` act on the destination's own
    directory entry and never write through a symlink to its target, so the previous entry
    survives intact until the replacement is complete -- while an unlink-then-write leaves a
    window with neither.

    `clobber=False` uses `link(2)`, which FAILS when the name already exists. That closes the gap
    between deciding a name is free and taking it: without it, an entry that appears in between
    is destroyed by a run that never decided to replace anything. Replacing something this run
    DID classify -- a shim of ours, or anything at all under `--force` -- stays a plain rename;
    re-checking there would only narrow a window, never close it, and narrowing a window is the
    kind of machinery that reads as a guarantee without being one.
    """
    handle, temporary = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            # By DESCRIPTOR, not by name. `mkstemp` opens O_EXCL at 0600 under an unpredictable
            # name, so nothing can be sitting there -- but resolving that name a second time
            # after the descriptor closes is a window that does not need to exist.
            os.fchmod(stream.fileno(), 0o755)
        if clobber:
            os.replace(temporary, dest)
        else:
            os.link(temporary, dest)
    except BaseException:
        discard_staging(temporary)
        raise
    if not clobber:
        # `link` leaves the staging name behind; `replace` consumed it.
        discard_staging(temporary)


def place(dest: Path, text: str, force: bool) -> str:
    """Install one managed name. Returns the action, and `foreign` when nothing was written."""
    data = text.encode("utf-8")
    if not os.path.lexists(dest):
        try:
            write_atomically(dest, data, clobber=False)
        except FileExistsError:
            # Something took the name between the check and the link. It was never classified,
            # so it is exactly the user-owned executable this must not overwrite.
            return "foreign"
        return "created"
    if is_ours(dest):
        # Content alone is not "installed": a shim that lost its execute bits is a command that
        # exits 126, and reporting success over that is the same lie as reporting it over a
        # missing file. Rewriting restores the mode as well as the bytes.
        if dest.read_bytes() == data and stat.S_IMODE(dest.stat().st_mode) == 0o755:
            return "already installed"
        action = "refreshed"
    elif force:
        action = "replaced"
    else:
        return "foreign"
    write_atomically(dest, data, clobber=True)
    return action


def interpreter_note() -> str:
    """What `python3` currently resolves to, as a stated fact rather than a check.

    The shim resolves `python3` in the user's own shell at run time; this installer cannot bind
    that, so refusing on what it happens to find here would be a guess wearing a gate's clothes.
    """
    found = shutil.which("python3")
    if found is None:
        return "python3 is NOT on this PATH -- code-limit needs one, version 3.11 or newer"
    try:
        done = subprocess.run([found, "-V"], capture_output=True, text=True, timeout=30)
        version = (done.stdout or done.stderr).strip() or "version not reported"
    except (OSError, subprocess.SubprocessError):
        version = "version not reported"
    return f"code-limit will run {found} ({version}); the report needs 3.11 or newer"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install `code-limit`, a shim onto this plugin's usage-limit report.")
    parser.add_argument("--bin-dir", default="~/.local/bin", metavar="DIR",
                        help="directory to install into (default: ~/.local/bin)")
    parser.add_argument("--force", action="store_true",
                        help="replace a managed name that is not this plugin's shim")
    args = parser.parse_args(argv)

    report = Path(__file__).resolve().parent / REPORT_NAME
    if not report.is_file():
        print(f"error: {report} is not a file -- nothing to install a shim onto", file=sys.stderr)
        return 1
    # A control character would be emitted into a shell file and, in the case of a newline, split
    # the one exec statement in two. Refused rather than escaped: no path here needs one. The same
    # predicate decides what counts as one of our shims, so the two languages cannot drift apart.
    if not emittable(str(report)):
        print(f"error: the report's path carries a control character: {report!r}", file=sys.stderr)
        return 1
    if in_version_cache(report):
        print(VERSION_CACHE_REFUSAL.format(report=report), file=sys.stderr)
        return 1

    bin_dir = Path(os.path.expanduser(args.bin_dir))
    if os.path.lexists(bin_dir) and not bin_dir.is_dir():
        print(f"error: --bin-dir {bin_dir} exists and is not a directory", file=sys.stderr)
        return 1
    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"error: cannot create --bin-dir {bin_dir}: {exc.strerror}", file=sys.stderr)
        return 1

    text = shim_text(report)
    managed = [PRIMARY] + [name for name in LEGACY if os.path.lexists(bin_dir / name)]

    print(f"report:  {report}")
    print(f"bin dir: {bin_dir}")
    foreign: list[Path] = []
    broken: list[str] = []
    for name in managed:
        dest = bin_dir / name
        try:
            action = place(dest, text, args.force)
        except OSError as exc:
            # A name that cannot be written -- a directory sitting there, a read-only bin dir --
            # is reported as the gap it is. Letting it out as a traceback would end the run past
            # every remaining name with no warning naming what did not happen.
            broken.append(f"{dest}: {exc.strerror or exc}")
            print(f"  {name}: FAILED -- {exc.strerror or exc}")
            continue
        if action == "foreign":
            foreign.append(dest)
            print(f"  {name}: left alone -- not this plugin's shim")
        else:
            print(f"  {name}: {action}")

    print(interpreter_note())
    # The bin directory IS shell code in this line, and a user is meant to paste it, so it is
    # quoted exactly as the report's path is inside the shim.
    if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"note: {bin_dir} is not on PATH. Add it, e.g.:\n"
              f"  export PATH={posix_quote(str(bin_dir))}:$PATH")

    if foreign or broken:
        print("\nwarnings")
        for dest in foreign:
            print(f"  {dest} is not this plugin's shim and was NOT replaced -- "
                  "re-run with --force to replace it, or remove it first")
        for failure in broken:
            print(f"  {failure} -- NOT installed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
