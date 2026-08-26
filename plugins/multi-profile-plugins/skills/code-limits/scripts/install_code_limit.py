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
import re
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

# Anchored over the WHOLE file, not a marker substring. A file that merely quotes the marker in a
# comment is somebody else's script, and treating it as ours would replace a user-owned executable
# with no confirmation -- the one thing the install step must never do. `.` does not match a
# newline here, so this admits exactly three lines and nothing longer.
SHIM_PATTERN = re.compile(
    r"#!/bin/sh\n" + re.escape(MARKER) + r"\nexec python3 '.+' " + re.escape('"$@"') + r"\n")


def posix_quote(text: str) -> str:
    """Single-quote for `sh`, ALWAYS -- one shape, whatever the path contains.

    `shlex.quote` leaves an ordinary path bare, which would make the shim's third line have two
    possible forms and a pin on it correspondingly weaker. A closing quote, a literal quote and a
    reopening quote is the POSIX way to carry a quote through single quotes.
    """
    return "'" + text.replace("'", "'\"'\"'") + "'"


def shim_text(report: Path) -> str:
    """The whole shim. One statement, and the report's path is the only thing interpolated."""
    return f"#!/bin/sh\n{MARKER}\nexec python3 {posix_quote(str(report))} \"$@\"\n"


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
    """Whether this installer wrote `path`. lstat FIRST: a symlink is never ours, whatever it
    points at -- following one would read a genuine shim through a link the user placed by hand
    and then replace that link without asking."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return SHIM_PATTERN.fullmatch(content) is not None


def write_atomically(dest: Path, text: str) -> None:
    """Write the shim beside `dest`, then move it into place with one `os.replace`.

    Deliberately NO unlink first. `rename(2)` replaces the destination's own directory entry and
    never writes through a symlink to its target, so the previous entry survives intact until the
    replacement is complete -- while an unlink-then-write leaves a window with neither.
    """
    handle, temporary = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temporary, 0o755)
        os.replace(temporary, dest)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def place(dest: Path, text: str, force: bool) -> str:
    """Install one managed name. Returns the action, and `foreign` when nothing was written."""
    if os.path.lexists(dest):
        if is_ours(dest):
            if dest.read_text(encoding="utf-8") == text:
                return "already installed"
            action = "refreshed"
        elif force:
            action = "replaced"
        else:
            return "foreign"
    else:
        action = "created"
    write_atomically(dest, text)
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
    # the one exec statement in two. Refused rather than escaped: no path here needs one.
    if any(ord(character) < 32 for character in str(report)):
        print(f"error: the report's path carries a control character: {report!r}", file=sys.stderr)
        return 1
    if in_version_cache(report):
        print(f"error: {report}\n"
              "  is inside a version-scoped plugin cache. Those directories name ONE version and "
              "are garbage-collected,\n"
              "  so a command installed from here silently keeps running an old report and then "
              "breaks when it is removed.\n"
              "  Run this installer from a version-stable copy instead -- the marketplace "
              "checkout,\n"
              "  ~/.claude/plugins/marketplaces/<marketplace>/.../skills/code-limits/scripts/, "
              "or a clone of the repo.", file=sys.stderr)
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
    for name in managed:
        dest = bin_dir / name
        action = place(dest, text, args.force)
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

    if foreign:
        print("\nwarnings")
        for dest in foreign:
            print(f"  {dest} is not this plugin's shim and was NOT replaced -- "
                  "re-run with --force to replace it, or remove it first")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
