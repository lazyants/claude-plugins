#!/usr/bin/env python3
"""The executable contract for `check_version_surfaces.py`'s `origin/main` baseline.

`.github/workflows/version-surfaces.yml` runs this file, and then the checker itself, on every
push to `main` and every pull request to `main` that touches a version surface or this directory.
Run it yourself as well after touching the checker -- CI only reports once the branch is pushed:

    python3 .claude/skills/plugin-repo-mechanics/scripts/check_version_surfaces.test.py -v

Each case builds a throwaway repository in a temp directory: no network, no fixtures on disk, and
`origin/main` is written straight into `refs/remotes/origin/main` rather than cloned, because the
checker only ever READS that ref and a real remote would add a failure mode the checker cannot see.

Two traps this file exists to stay out of:

  - **Every fixture carries at least two plugins.** `MIN_PLAUSIBLE_PLUGINS` makes the checker exit
    2 on a smaller sweep, and exit 2 with no output read is indistinguishable from a case that
    passed for the reason the test claims.
  - **A case that passes the UNMODIFIED checker proves nothing about the baseline.** Those exist
    here on purpose -- they are false-RED guards, asserting the baseline does NOT fire -- and are
    named so, so nobody later reads them as evidence the baseline works.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check_version_surfaces.py"

# Imported as well as run, for the one predicate whose two answers cannot both be produced on one
# machine: a case-folding filesystem and a case-sensitive one. Driving the whole checker would pin
# only whichever half this runner happens to be, and the other half is where the bug was.
_spec = importlib.util.spec_from_file_location("check_version_surfaces", SCRIPT)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def filesystem_folds() -> bool:
    """Whether the filesystem these fixtures are built on treats two spellings as one file.

    Measured here, on a temp directory, because that is where every fixture lives -- and because
    the cases below split on it: a collision is only a collision where the checkout cannot hold
    both spellings. Asserting a folding outcome on Linux is how the shipped suite came to fail on
    the very platform its own documented command was run on.
    """
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "plugins").mkdir()
        return checker.folds_paths(Path(tmp))


FOLDS_PATHS = filesystem_folds()
ONLY_FOLDING = unittest.skipUnless(FOLDS_PATHS, "this filesystem keeps the two spellings apart")


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=GIT_ENV)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def git_try(repo: Path, *args: str) -> int:
    """A git call whose non-zero exit is the point -- a conflicting merge, for instance."""
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=GIT_ENV).returncode


def write_tree(repo: Path, versions: dict[str, str]) -> None:
    """Lay down a tree every surface of which agrees, for each plugin in `versions`.

    Built to the same shapes the checker parses -- anchor slug included -- so a fixture that stops
    matching is a real signal about the parser and not about this file.
    """
    (repo / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (repo / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"plugins": [{"name": n, "version": v} for n, v in versions.items()]}), encoding="utf-8")
    rows, sections, entries = [], [], []
    for name, version in versions.items():
        anchor = f"{name}--v{version.replace('.', '')}"
        rows.append(f"| [`{name}`](#{anchor}) | {version} |")
        sections.append(f"## `{name}` — v{version}\n\nprose.\n")
        entries.append(f"## [{name} {version}] - 2026-01-01\n\n- a change.\n")
        manifest = repo / "plugins" / name / ".claude-plugin"
        manifest.mkdir(parents=True, exist_ok=True)
        (manifest / "plugin.json").write_text(json.dumps({"name": name, "version": version}), encoding="utf-8")
    (repo / "README.md").write_text(
        "# repo\n\n| plugin | version |\n| --- | --- |\n" + "\n".join(rows) + "\n\n" + "\n".join(sections),
        encoding="utf-8")
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n" + "\n".join(entries), encoding="utf-8")


def commit_index(repo: Path, message: str) -> str:
    """Commit exactly what the INDEX holds, adding nothing. The case-only fixture needs this:
    `git add -A` would put the on-disk spelling straight back."""
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def commit(repo: Path, message: str) -> str:
    """`commit_index` plus stage-everything, which is the whole difference between the two."""
    git(repo, "add", "-A")
    return commit_index(repo, message)


def run(repo: Path) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo)],
                          capture_output=True, text=True, env=GIT_ENV)
    return proc.returncode, proc.stdout + proc.stderr


class BaselineCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def init(self) -> None:
        git(self.repo, "init", "-q", "-b", "main")

    def publish_head(self, message: str, index_only: bool = False) -> str:
        """Commit what is here, and make that commit what `origin/main` publishes.

        Every fixture in this file needs exactly this pair, and spelling it out per case was three
        different spellings of one operation by the time there were a dozen of them.
        """
        sha = (commit_index if index_only else commit)(self.repo, message)
        git(self.repo, "update-ref", "refs/remotes/origin/main", sha)
        return sha

    def publish(self, versions: dict[str, str], message: str = "published") -> str:
        """Write `versions`, then publish them."""
        write_tree(self.repo, versions)
        return self.publish_head(message)

    def branch_cut_at(self, base: str, published: str, branch: str, message: str = "published") -> None:
        """A branch cut where alpha stamped `base`, with `published` landing on origin/main after.

        The shape six cases open with. Only the two version numbers differ between them, so only
        those stay at the call sites; what is factored out is git mechanics identical in all six.
        """
        self.init()
        write_tree(self.repo, {"alpha": base, "beta": "9.0.0"})
        branch_point = commit(self.repo, "base")
        self.publish({"alpha": published, "beta": "9.0.0"}, message)
        git(self.repo, "checkout", "-q", "-b", branch, branch_point)


class Refusals(BaselineCase):
    """Trees the baseline must refuse. Each fails against the checker as it stood before it."""

    def test_release_branch_stamped_below_published_is_refused(self) -> None:
        self.branch_cut_at("1.0.0", "1.2.0", "feature", "sibling release lands first")
        write_tree(self.repo, {"alpha": "1.1.0", "beta": "9.0.0"})
        commit(self.repo, "our release, cut before the sibling landed")

        code, out = run(self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("would publish a DOWNGRADE", out)
        self.assertIn("stamps 1.1.0", out)          # both numbers named, per the issue
        self.assertIn("already publishes 1.2.0", out)
        self.assertIn("2 compared, 0 NOT COMPARED", out)   # refused, but the comparison DID happen

    def test_version_order_is_numeric_not_lexical(self) -> None:
        """`"1.9.0" > "1.10.0"` as strings. A string compare would call this tree ahead."""
        self.branch_cut_at("1.8.0", "1.10.0", "feature")
        write_tree(self.repo, {"alpha": "1.9.0", "beta": "9.0.0"})
        commit(self.repo, "bump")

        code, out = run(self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("this tree stamps 1.9.0, origin/main already publishes 1.10.0", out)

    def test_a_real_merge_resolved_ours_clobbers_the_sibling_bump(self) -> None:
        """The conflict resolution the skill's docs warn about, as an actual two-parent merge.

        A previous version of this case faked the end state with an ordinary child commit. It
        asserted the same values, but it never proved the rule holds over real merge topology --
        which is where the merge base stops being "the branch point" and becomes `origin/main`
        itself. Built as a genuine `git merge` with `--ours` resolution for that reason.
        """
        self.branch_cut_at("1.0.0", "1.2.0", "feature", "the sibling release")
        write_tree(self.repo, {"alpha": "1.1.0", "beta": "9.0.0"})
        commit(self.repo, "our own bump, cut before the sibling landed")

        self.assertNotEqual(git_try(self.repo, "merge", "--no-edit", "origin/main"), 0,
                            "fixture must actually conflict, or it is not testing a resolution")
        git(self.repo, "checkout", "-q", "--ours", "--", ".")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "--no-edit")

        parents = git(self.repo, "rev-list", "--parents", "-n", "1", "HEAD").split()
        self.assertEqual(len(parents), 3, "HEAD must be a real two-parent merge commit")
        self.assertEqual(git(self.repo, "merge-base", "HEAD", "origin/main"),
                         git(self.repo, "rev-parse", "origin/main"))

        code, out = run(self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("would publish a DOWNGRADE", out)
        self.assertIn("stamps 1.1.0", out)
        self.assertIn("already publishes 1.2.0", out)


class Allowances(BaselineCase):
    """Trees the baseline must NOT refuse. These are false-RED guards: they pass the unmodified
    checker too, by construction. Their value is that they go RED the moment someone implements
    the issue's own proposal -- refuse unless the version is strictly greater -- which would reject
    roughly half of this repo's merges."""

    def test_stale_branch_that_never_moved_the_version_is_allowed(self) -> None:
        self.branch_cut_at("1.0.0", "1.2.0", "content")
        (self.repo / "notes.md").write_text("content work, no bump\n", encoding="utf-8")
        commit(self.repo, "content only")

        code, out = run(self.repo)
        self.assertEqual(code, 0, out)
        self.assertNotIn("DOWNGRADE", out)
        self.assertIn("2 compared, 0 NOT COMPARED", out)

    def test_bump_then_revert_ends_at_the_merge_base_and_is_allowed(self) -> None:
        """`head == base` is the test, not "never touched the file": a reverted bump moves nothing."""
        self.branch_cut_at("1.0.0", "1.2.0", "wobble")
        write_tree(self.repo, {"alpha": "1.1.0", "beta": "9.0.0"})
        commit(self.repo, "bump")
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        commit(self.repo, "revert the bump")

        code, out = run(self.repo)
        self.assertEqual(code, 0, out)
        self.assertNotIn("DOWNGRADE", out)

    def test_a_tree_ahead_of_published_is_allowed(self) -> None:
        self.init()
        write_tree(self.repo, {"alpha": "1.8.0", "beta": "9.0.0"})
        commit(self.repo, "base")
        self.publish({"alpha": "1.9.0", "beta": "9.0.0"})
        git(self.repo, "checkout", "-q", "-b", "release")
        write_tree(self.repo, {"alpha": "1.10.0", "beta": "9.0.0"})
        commit(self.repo, "ordinary release")

        code, out = run(self.repo)
        self.assertEqual(code, 0, out)
        self.assertIn("2 compared, 0 NOT COMPARED", out)


class NotCompared(BaselineCase):
    """"Could not compare" and "compared and agreed" are the pair the old green line conflated.
    Every unavailable comparison must say which unavailability it was."""

    def test_no_origin_main_ref_says_so_instead_of_reading_clean(self) -> None:
        self.init()
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        commit(self.repo, "only local history, no remote")

        code, out = run(self.repo)
        self.assertEqual(code, 0, out)
        self.assertIn("0 compared, 2 NOT COMPARED", out)
        self.assertIn("no usable origin/main commit", out)

    def test_repo_that_is_not_a_git_checkout_at_all(self) -> None:
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})

        code, out = run(self.repo)
        self.assertEqual(code, 0, out)          # not unsoundness: exit 2 is for an unreadable TREE
        self.assertIn("NOT COMPARED", out)
        # A distinct reason from "you have a checkout but no origin/main": these are different
        # things to go and fix, and an earlier version of this test asserted the wrong one.
        self.assertIn("not a git checkout", out)
        self.assertNotIn("no usable origin/main commit", out)

    def test_plugin_absent_from_origin_main_has_no_baseline(self) -> None:
        self.init()
        self.publish({"alpha": "1.0.0", "beta": "9.0.0"})
        git(self.repo, "checkout", "-q", "-b", "new-plugin")
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0", "gamma": "0.1.0"})
        commit(self.repo, "add a brand-new plugin")

        code, out = run(self.repo)
        self.assertEqual(code, 0, out)
        self.assertIn("2 compared, 1 NOT COMPARED", out)
        self.assertIn("no manifest on origin/main yet", out)

    def test_a_baseline_manifest_git_cannot_parse_is_not_called_absent(self) -> None:
        """"Absent" is a claim about the tree, and it is the claim that waves a downgrade through.
        A manifest that IS on origin/main and merely unreadable must not borrow that word."""
        self.init()
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        (self.repo / "plugins" / "alpha" / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "alpha", "version": "1.0.0.0"}), encoding="utf-8")
        sha = self.publish_head("a four-part version on the published side")
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        commit(self.repo, "our tree is well-formed")

        code, out = run(self.repo)
        self.assertEqual(code, 0, out)
        self.assertIn("not readable as an X.Y.Z version", out)
        self.assertNotIn("has no manifest on origin/main yet", out)

    def test_undecodable_baseline_bytes_do_not_crash_the_run(self) -> None:
        """`text=True` raised UnicodeDecodeError out of subprocess itself -- not an OSError, so it
        escaped as a traceback under exit 1, the code that means a real disagreement."""
        self.init()
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        (self.repo / "plugins" / "alpha" / ".claude-plugin" / "plugin.json").write_bytes(b'{"version": "\xff\xfe"}')
        sha = self.publish_head("undecodable bytes on the published side")
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        commit(self.repo, "our tree is well-formed")

        code, out = run(self.repo)
        self.assertEqual(code, 0, out)
        self.assertIn("not readable as an X.Y.Z version", out)
        self.assertNotIn("Traceback", out)

    def test_an_agreed_but_invalid_version_is_not_tallied_as_compared(self) -> None:
        """All five surfaces saying `1.0` is agreement, not a version. Counting it as compared
        would be the summary lying about its own coverage."""
        self.init()
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        sha = self.publish_head("base")
        readme = (self.repo / "README.md").read_text(encoding="utf-8")
        (self.repo / "README.md").write_text(readme.replace("1.0.0", "1.0").replace("v100", "v10"), encoding="utf-8")
        for path in (self.repo / ".claude-plugin" / "marketplace.json",
                     self.repo / "plugins" / "alpha" / ".claude-plugin" / "plugin.json"):
            path.write_text(path.read_text(encoding="utf-8").replace('"1.0.0"', '"1.0"'), encoding="utf-8")
        commit(self.repo, "every surface agrees on something that is not a version")

        code, out = run(self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("which is not an X.Y.Z version", out)
        self.assertIn("1 compared, 1 NOT COMPARED", out)
        self.assertIn("do not agree on one valid X.Y.Z version", out)

    def test_unrelated_history_has_no_merge_base(self) -> None:
        self.init()
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        commit(self.repo, "our history")
        git(self.repo, "checkout", "-q", "--orphan", "published")
        write_tree(self.repo, {"alpha": "1.2.0", "beta": "9.0.0"})
        sha = self.publish_head("an unrelated published history")
        git(self.repo, "checkout", "-q", "main")

        self.assertNotEqual(subprocess.run(["git", "-C", str(self.repo), "merge-base", "HEAD", "origin/main"],
                                           capture_output=True, env=GIT_ENV).returncode, 0,
                            "fixture must genuinely have no merge base")
        code, out = run(self.repo)
        self.assertEqual(code, 0, out)
        self.assertIn("1 NOT COMPARED", out)
        self.assertIn("could not establish a merge base", out)

    def test_disagreeing_surfaces_report_the_baseline_as_not_compared(self) -> None:
        """The gap codex's plan review named: exit 1 for the disagreement must not leave the
        baseline's status to be inferred from a zero."""
        self.branch_cut_at("1.0.0", "1.2.0", "partial")
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        manifest = self.repo / "plugins" / "alpha" / ".claude-plugin" / "plugin.json"
        manifest.write_text(json.dumps({"name": "alpha", "version": "1.1.0"}), encoding="utf-8")
        commit(self.repo, "bumped the manifest only")

        code, out = run(self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("surfaces disagree", out)
        self.assertIn("1 compared, 1 NOT COMPARED", out)
        self.assertIn("nothing to compare", out)
        # `emit` escapes every non-printable byte, so a newline folded into a NOT_COMPARED reason
        # would reach the terminal as a literal backslash-n mid-sentence. No test caught that; a
        # human reading the output did. This is the guard that reads it from now on.
        self.assertNotIn(r"\n", out)


class BaselineTopology(BaselineCase):
    """`plugins/` read through the filesystem and read out of a git tree are not the same thing:
    the filesystem FOLLOWS a symlink, `git show <ref>:a/b/c` does not, and neither walks into a
    submodule gitlink. Where the BASELINE side is behind one of those, the sweep cannot learn what
    is published and must say so -- exit 2 -- rather than report the plugin as one that has no
    baseline yet, which is the one answer that waves a real downgrade through.

    Every case here is a different DEPTH of the same shape. They are cheap to add because the
    checker no longer enumerates depths: it reads one recursive listing and answers out of it."""

    def publish_with(self, build) -> None:
        """Commit a tree `build` shapes and publish it as `origin/main`.

        What the CURRENT tree looks like afterwards is each case's own business, and they differ:
        the directory cases unlink the symlink first, the leaf case does not, so its `write_tree`
        follows the symlink and writes through it. Either is fine -- the baseline side is what
        decides these, and saying so here stops the next reader inferring a shape none of them
        actually share."""
        self.init()
        write_tree(self.repo, {"alpha": "2.0.0", "beta": "9.0.0"})
        build()
        sha = self.publish_head("published through a topology git will not walk")

    @ONLY_FOLDING
    def test_a_baseline_rooted_at_another_spelling_is_still_found(self) -> None:
        """The listing is no longer scoped by an exact `-- plugins` pathspec. It was, and `ls-tree`
        takes no `:(icase)` magic, so a baseline rooted at `PLUGINS` came back EMPTY -- which reads
        as "no plugin has a baseline at all" and waves every downgrade through at once."""
        self.init()
        write_tree(self.repo, {"alpha": "2.0.0", "beta": "9.0.0"})
        commit(self.repo, "base")
        listing = git(self.repo, "ls-tree", "-r", "--full-tree", "HEAD").splitlines()
        git(self.repo, "rm", "-r", "-q", "--cached", "plugins")
        for line in listing:
            meta, _, path = line.partition("\t")
            mode, _, oid = meta.split()
            if path.startswith("plugins/"):
                git(self.repo, "update-index", "--add", "--cacheinfo",
                    f"{mode},{oid},PLUGINS/{path[len('plugins/'):]}")
        sha = self.publish_head("published under a differently-spelled root", index_only=True)
        self.assertTrue(any(p.startswith("PLUGINS/") for p in
                            git(self.repo, "ls-tree", "-r", "--name-only", sha).splitlines()),
                        "fixture must really root the baseline at the other spelling")
        git(self.repo, "read-tree", "--reset", "-u", "HEAD~1")
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        commit(self.repo, "a downgrade the exact pathspec used to hide")

        code, out = run(self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("would publish a DOWNGRADE", out)
        self.assertNotIn("has no manifest on origin/main yet", out)

    def test_symlinked_plugins_root(self) -> None:
        store = self.repo / "store"
        store.mkdir()
        self.init()
        os.symlink("store", self.repo / "plugins")
        write_tree(self.repo, {"alpha": "2.0.0", "beta": "9.0.0"})
        sha = self.publish_head("published through a symlinked plugins/")
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        commit(self.repo, "a downgrade the symlink would hide")

        code, out = run(self.repo)
        self.assertEqual(code, 2, out)
        self.assertIn("plugins is mode 120000 on origin/main", out)
        self.assertNotIn("has no manifest on origin/main yet", out)
        self.assertEqual(out.count("UNSOUND: plugins is mode"), 1, "one tree problem, one line")

    def test_symlinked_plugin_directory(self) -> None:
        def build() -> None:
            shutil.rmtree(self.repo / "plugins" / "alpha")
            (self.repo / "vendored" / ".claude-plugin").mkdir(parents=True)
            (self.repo / "vendored" / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "alpha", "version": "2.0.0"}), encoding="utf-8")
            os.symlink("../vendored", self.repo / "plugins" / "alpha")
        self.publish_with(build)
        (self.repo / "plugins" / "alpha").unlink()
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        commit(self.repo, "a downgrade behind a symlinked plugin directory")

        code, out = run(self.repo)
        self.assertEqual(code, 2, out)
        self.assertIn("plugins/alpha is mode 120000 on origin/main", out)

    def test_symlinked_claude_plugin_directory(self) -> None:
        """The depth round 2 found: the guard checked `plugins` and `plugins/<name>` and stopped."""
        def build() -> None:
            shutil.rmtree(self.repo / "plugins" / "alpha" / ".claude-plugin")
            (self.repo / "vendored").mkdir()
            (self.repo / "vendored" / "plugin.json").write_text(
                json.dumps({"name": "alpha", "version": "2.0.0"}), encoding="utf-8")
            os.symlink("../../vendored", self.repo / "plugins" / "alpha" / ".claude-plugin")
        self.publish_with(build)
        (self.repo / "plugins" / "alpha" / ".claude-plugin").unlink()
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        commit(self.repo, "a downgrade behind a symlinked .claude-plugin")

        code, out = run(self.repo)
        self.assertEqual(code, 2, out)
        self.assertIn("plugins/alpha/.claude-plugin is mode 120000 on origin/main", out)
        self.assertNotIn("has no manifest on origin/main yet", out)

    def test_symlinked_manifest_leaf(self) -> None:
        """One level deeper again -- and this one git DOES hand over: it returns the symlink's
        target text, which parses as neither JSON nor a version."""
        def build() -> None:
            (self.repo / "plugins" / "alpha" / ".claude-plugin" / "plugin.json").unlink()
            (self.repo / "vendored.json").write_text(
                json.dumps({"name": "alpha", "version": "2.0.0"}), encoding="utf-8")
            os.symlink("../../../vendored.json",
                       self.repo / "plugins" / "alpha" / ".claude-plugin" / "plugin.json")
        self.publish_with(build)
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        commit(self.repo, "a downgrade behind a symlinked manifest")

        code, out = run(self.repo)
        self.assertEqual(code, 2, out)
        self.assertIn("plugin.json is mode 120000 on origin/main, not a regular file", out)

    def stage_blob(self, version: str, path: str) -> None:
        """Put one manifest blob into the index under an arbitrary spelling.

        Plumbing, because `git mv plugins/alpha plugins/Alpha` cannot run here at all -- which is
        itself the point: the checkout cannot hold both spellings, and the tree can.
        """
        (self.repo / "spelling.json").write_text(
            json.dumps({"name": "alpha", "version": version}), encoding="utf-8")
        blob = git(self.repo, "hash-object", "-w", "spelling.json")
        git(self.repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")

    @ONLY_FOLDING
    def test_one_differing_spelling_is_used_not_refused(self) -> None:
        """A single spelling is unambiguous however it is capitalised: the checkout materialises
        that file under that name, so it IS this plugin's baseline and the downgrade is caught."""
        self.init()
        write_tree(self.repo, {"alpha": "2.0.0", "beta": "9.0.0"})
        commit(self.repo, "base")
        git(self.repo, "rm", "-r", "-q", "--cached", "plugins/alpha")
        self.stage_blob("2.0.0", "plugins/Alpha/.claude-plugin/plugin.json")
        sha = self.publish_head("published under the other spelling", index_only=True)
        git(self.repo, "rm", "-q", "--cached", "plugins/Alpha/.claude-plugin/plugin.json")
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        commit(self.repo, "a downgrade under the spelling this filesystem uses")

        code, out = run(self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("would publish a DOWNGRADE", out)
        self.assertIn("already publishes 2.0.0", out)

    @ONLY_FOLDING
    def test_several_colliding_spellings_are_refused(self) -> None:
        """The tree says three files, the checkout can hold one, and which one it holds is decided
        by tree order. The exact spelling being among them is exactly when that goes unnoticed."""
        self.init()
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        commit(self.repo, "base with the exact spelling at 1.0.0")
        self.stage_blob("3.0.0", "plugins/ALPHA/.claude-plugin/plugin.json")
        self.stage_blob("2.0.0", "plugins/Alpha/.claude-plugin/plugin.json")
        sha = self.publish_head("three spellings, three versions", index_only=True)
        listing = git(self.repo, "ls-tree", "-r", "--name-only", sha)
        self.assertEqual(sum("alpha" in line.lower() for line in listing.splitlines()), 3,
                         "fixture must really record three spellings")
        git(self.repo, "rm", "-q", "--cached",
            "plugins/ALPHA/.claude-plugin/plugin.json", "plugins/Alpha/.claude-plugin/plugin.json")
        commit(self.repo, "the branch keeps only the exact spelling, still at 1.0.0")

        code, out = run(self.repo)
        self.assertEqual(code, 2, out)
        self.assertIn("3 spellings of this manifest", out)
        self.assertNotIn("has no manifest on origin/main yet", out)

    def test_a_topology_the_MERGE_BASE_hides_is_named_as_the_merge_base(self) -> None:
        """The equality exemption reads the merge base, so the merge base has to be walkable too --
        and the message has to say WHICH ref it could not walk, or it reads as a claim about
        origin/main."""
        self.init()
        (self.repo / "vendored" / ".claude-plugin").mkdir(parents=True)
        (self.repo / "vendored" / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "alpha", "version": "1.0.0"}), encoding="utf-8")
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        shutil.rmtree(self.repo / "plugins" / "alpha")
        os.symlink("../vendored", self.repo / "plugins" / "alpha")
        branch_point = commit(self.repo, "the merge base reaches alpha through a symlink")
        (self.repo / "plugins" / "alpha").unlink()
        self.publish({"alpha": "2.0.0", "beta": "9.0.0"}, "an ordinary tree lands on main")
        git(self.repo, "checkout", "-q", "-b", "behind", branch_point)
        (self.repo / "plugins" / "alpha").unlink()
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        commit(self.repo, "still at 1.0.0, which is now behind")

        code, out = run(self.repo)
        self.assertEqual(code, 2, out)
        self.assertIn("on the merge base", out)
        self.assertNotIn("mode 120000 on origin/main", out)

    def test_a_working_tree_symlink_does_not_stop_the_refusal(self) -> None:
        """The other side, and the reason the working tree is NOT checked for symlinks: what the
        filesystem reads there IS what a checkout of the merged result would read, so it is the
        right thing to compare. Only the baseline side has to be walkable."""
        self.init()
        write_tree(self.repo, {"alpha": "2.0.0", "beta": "9.0.0"})
        sha = self.publish_head("published as ordinary directories")
        older = self.repo / "vendored" / ".claude-plugin"
        older.mkdir(parents=True)
        (older / "plugin.json").write_text(json.dumps({"name": "alpha", "version": "1.0.0"}), encoding="utf-8")
        shutil.rmtree(self.repo / "plugins" / "alpha")
        os.symlink("../vendored", self.repo / "plugins" / "alpha")
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        commit(self.repo, "the working tree reaches 1.0.0 through a symlink")

        code, out = run(self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("would publish a DOWNGRADE", out)
        self.assertIn("already publishes 2.0.0", out)


class PathCollisionSemantics(unittest.TestCase):
    """The collision predicate is the one thing here whose correct answer differs BY MACHINE.

    macOS folds case and normalization, Linux folds neither, and this repository is cloned on both.
    These drive the predicate directly with each answer, so both halves are pinned wherever the
    suite runs; the probe case is what ties them back to the filesystem actually underfoot.
    """

    ALPHA = "plugins/alpha/.claude-plugin/plugin.json"
    CAPPED = "plugins/Alpha/.claude-plugin/plugin.json"

    def test_the_probe_agrees_with_this_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "plugins").mkdir()
            twin = repo / "PLUGINS"
            measured = twin.exists() and os.path.samefile(repo / "plugins", twin)
            self.assertEqual(checker.folds_paths(repo), measured,
                             "the probe must report what this filesystem does, not what it was built on")
        with tempfile.TemporaryDirectory() as bare:
            # Nothing to probe means no answer, on every platform. Asserted because the rest of
            # this case can only distinguish the two answers on a machine that gives the OTHER one:
            # on a folding filesystem `measured` is True and so is a probe hardwired to True.
            self.assertFalse(checker.folds_paths(Path(bare)))

    def test_the_probe_refuses_a_twin_the_checkout_can_alias(self) -> None:
        """`samefile` follows symlinks, so a `PLUGINS -> plugins` entry -- which any contributor can
        add, tracked or not -- used to make a case-SENSITIVE filesystem answer "I fold". The answer
        decides whether one plugin may adopt another's manifest, so it must not be aliasable."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "plugins").mkdir()
            try:
                # Guarded on the attempt, not on FOLDS_PATHS: the question here is whether THIS
                # directory can hold both names, and asking the filesystem is cheaper than trusting
                # a constant measured somewhere else to still describe it.
                os.symlink("plugins", repo / "PLUGINS")
            except FileExistsError:
                self.skipTest("the twin name already resolves to the probe on this filesystem")
            self.assertTrue(os.path.samefile(repo / "plugins", repo / "PLUGINS"),
                            "fixture must reproduce the alias the old probe trusted")
            self.assertFalse(checker.folds_paths(repo))

    def test_a_folding_ancestor_is_topology_not_absence(self) -> None:
        """`plugins/Alpha` IS this plugin's directory where the filesystem folds, so a symlink there
        hides a published baseline. Probing the exact lowercase key found nothing and called that
        absent -- the one answer that lets a downgrade through."""
        index = {"plugins": ("040000", "t"), "plugins/Alpha": ("120000", "link")}
        self.assertEqual(checker.baseline_blob(index, "alpha", True)[1], "topology")
        self.assertEqual(checker.baseline_blob(index, "alpha", False)[1], "absent")

    def test_folding_and_exact_disagree_about_two_spellings(self) -> None:
        self.assertTrue(checker.same_path(self.ALPHA, self.CAPPED, True))
        self.assertFalse(checker.same_path(self.ALPHA, self.CAPPED, False))
        self.assertTrue(checker.same_path(self.ALPHA, self.ALPHA, False))

    def test_a_case_sensitive_checkout_does_not_adopt_another_spelling(self) -> None:
        """The finding: with one differently-cased entry, folding ADOPTS it as this plugin's
        baseline. On a case-sensitive checkout those are two plugins and there is no baseline."""
        index = {"plugins": ("040000", "t"), "plugins/Alpha": ("040000", "t"),
                 "plugins/Alpha/.claude-plugin": ("040000", "t"), self.CAPPED: ("100644", "blob0")}
        self.assertEqual(checker.baseline_blob(index, "alpha", True), ("blob0", checker.COMPARED, ""))
        self.assertEqual(checker.baseline_blob(index, "alpha", False)[:2], (None, "absent"))

    def test_a_case_sensitive_checkout_does_not_call_two_paths_a_collision(self) -> None:
        index = {"plugins": ("040000", "t"),
                 "plugins/alpha": ("040000", "t"), "plugins/alpha/.claude-plugin": ("040000", "t"),
                 self.ALPHA: ("100644", "mine"),
                 "plugins/Alpha": ("040000", "t"), "plugins/Alpha/.claude-plugin": ("040000", "t"),
                 self.CAPPED: ("100644", "theirs")}
        self.assertEqual(checker.baseline_blob(index, "alpha", True)[1], "topology")
        self.assertEqual(checker.baseline_blob(index, "alpha", False), ("mine", checker.COMPARED, ""))


class UntouchedContract(BaselineCase):
    """The baseline must not have moved anything the checker already promised."""

    def test_internal_disagreement_still_exits_one(self) -> None:
        self.init()
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        (self.repo / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"plugins": [{"name": "alpha", "version": "0.9.0"}, {"name": "beta", "version": "9.0.0"}]}),
            encoding="utf-8")
        self.publish_head("marketplace out of step")

        code, out = run(self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("surfaces disagree", out)

    def test_missing_changelog_entry_still_exits_one(self) -> None:
        self.init()
        write_tree(self.repo, {"alpha": "1.0.0", "beta": "9.0.0"})
        (self.repo / "CHANGELOG.md").write_text("# Changelog\n\n## [beta 9.0.0] - 2026-01-01\n\n- a change.\n",
                                                encoding="utf-8")
        sha = self.publish_head("changelog entry never written")

        code, out = run(self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("has no entry for 1.0.0", out)

    def test_an_implausibly_small_sweep_is_still_unsound(self) -> None:
        self.init()
        write_tree(self.repo, {"alpha": "1.0.0"})
        sha = self.publish_head("one plugin only")

        code, out = run(self.repo)
        self.assertEqual(code, 2, out)
        self.assertIn("refusing to report that clean", out)

    def test_the_real_repository_is_clean(self) -> None:
        """The checker's own tree, which is the state every release is cut from."""
        code, out = run(SCRIPT.resolve().parents[4])
        self.assertEqual(code, 0, out)
        self.assertIn("0 disagreeing", out)


if __name__ == "__main__":
    unittest.main()
