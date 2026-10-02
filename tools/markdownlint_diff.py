#!/usr/bin/env python3
"""Compare Markdown warning counts without exposing source files to a fixer."""

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    ).stdout


def baseline_files(repo, revision):
    files = {}
    for entry in git(repo, "ls-tree", "-rz", "--full-tree", revision).split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        path = raw_path.decode("utf-8")
        if not path.endswith(".md"):
            continue
        mode, kind, oid = metadata.split()
        if kind != b"blob" or mode not in (b"100644", b"100755"):
            raise ValueError(f"Not a regular Markdown file in baseline: {path}")
        files[path] = git(repo, "cat-file", "blob", oid.decode("ascii"))
    return files


def working_files(repo):
    files = {}
    paths = git(
        repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
        "--", "*.md",
    ).split(b"\0")
    for raw_path in paths:
        if not raw_path:
            continue
        path = raw_path.decode("utf-8")
        source = repo / path
        if source.is_symlink():
            raise ValueError(f"Not a regular Markdown file in working tree: {path}")
        if not source.exists():  # A tracked deletion is part of the comparison.
            continue
        if not source.is_file():
            raise ValueError(f"Not a regular Markdown file in working tree: {path}")
        files[path] = source.read_bytes()
    return files


def lint_counts(files, executable):
    if not files:
        return Counter()
    with tempfile.TemporaryDirectory(prefix="markdownlint-diff-") as directory:
        snapshot = Path(directory)
        # Flat numeric names avoid treating source filenames as globs or options.
        names = {}
        for index, (path, content) in enumerate(sorted(files.items())):
            name = f"{index}.md"
            (snapshot / name).write_bytes(content)
            names[name] = path
        config = snapshot / "config.json"
        config.write_text('{"default": true}\n', encoding="utf-8")
        result = subprocess.run(
            [executable, "--json", "--config", str(config), "--", *names],
            cwd=snapshot, capture_output=True, text=True,
        )
        if result.returncode not in (0, 1):
            raise ValueError(f"markdownlint failed ({result.returncode}): {result.stderr.strip()}")
        # markdownlint-cli writes diagnostics to stderr, including JSON mode.
        raw = result.stderr.strip() or result.stdout.strip() or "[]"
        diagnostics = json.loads(raw)
        if not isinstance(diagnostics, list):
            raise ValueError("markdownlint did not return a JSON diagnostic array")
        if (result.returncode == 1) != bool(diagnostics):
            raise ValueError("markdownlint exit status does not match its diagnostics")
        counts = Counter()
        for diagnostic in diagnostics:
            name = diagnostic["fileName"]
            rules = diagnostic["ruleNames"]
            if name not in names or not isinstance(rules, list) or not rules:
                raise ValueError("markdownlint returned an unknown file or invalid rule")
            counts[(names[name], rules[0])] += 1
        return counts


def compare(repo, base, executable):
    revision = git(repo, "merge-base", "HEAD", base).decode("ascii").strip()
    before = baseline_files(repo, revision)
    after = working_files(repo)
    old_counts = lint_counts(before, executable)
    new_counts = lint_counts(after, executable)
    print(f"Merge base: {revision}")
    print(f"Markdown files: baseline={len(before)}, working={len(after)}")
    print(f"Warnings: baseline={old_counts.total()}, working={new_counts.total()}")
    changes = 0
    for path, rule in sorted(old_counts.keys() | new_counts.keys()):
        key = (path, rule)
        delta = new_counts[key] - old_counts[key]
        if delta:
            changes += 1
            print(f"{delta:+d} {rule} {path}: {old_counts[key]} -> {new_counts[key]}")
    print(f"Changed file/rule counts: {changes}")
    print("Review deltas manually; equal counts do not prove unchanged content.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="merge-base ref (default: origin/main)")
    args = parser.parse_args(argv)
    try:
        executable = shutil.which("markdownlint")
        if executable is None:
            raise ValueError("markdownlint-cli is required (npm install -g markdownlint-cli@0.46.0)")
        repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode("utf-8").strip())
        compare(repo, args.base, executable)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(f"markdownlint-diff: {error}", file=sys.stderr)
        return 2
    # A report, not a style gate: existing house style may add intentional warnings.
    return 0


if __name__ == "__main__":
    sys.exit(main())
