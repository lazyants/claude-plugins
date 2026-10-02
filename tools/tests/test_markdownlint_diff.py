"""Exercise the read-only report and its real markdownlint-cli integration."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "markdownlint_diff.py"
SPEC = importlib.util.spec_from_file_location("markdownlint_diff", SCRIPT)
md = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(md)


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", "--initial-branch=tip", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "fixture@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Fixture"], check=True)
    return tmp_path


def commit(repo):
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)


@pytest.fixture
def markdownlint():
    executable = shutil.which("markdownlint")
    if executable is None:
        pytest.skip("real CLI tests require markdownlint-cli; markdownlint-diff CI installs it")
    return executable


def test_real_report_preserves_issue_refs_code_spans_and_changelog(repo, markdownlint):
    sources = {
        ".claude/skills/sample/SKILL.md": b"# Skill\n\nContinuation mentions\n#243 competitors universe.\n\n`{{SOURCE_LANG}} ` `placeholder`\n",
        "CHANGELOG.md": b"# Changelog\n\n- " + b"a" * 100 + b"\n",
        "space [literal].md": b"# Filename\n\nText.\n",
    }
    for name, content in sources.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    commit(repo)
    # Deliberately add warnings to test working-tree and untracked-file coverage.
    skill = repo / ".claude/skills/sample/SKILL.md"
    skill.write_bytes(sources[".claude/skills/sample/SKILL.md"] + b"\n#412 another literal reference.\n")
    (repo / "new.md").write_text("# New\n\n#582 literal text.\n", encoding="utf-8")
    before = {p.relative_to(repo): p.read_bytes() for p in repo.rglob("*.md")}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--base", "HEAD"], cwd=repo,
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "+1 MD018 .claude/skills/sample/SKILL.md" in result.stdout
    assert "+1 MD018 new.md" in result.stdout
    assert "Markdown files: baseline=3, working=4" in result.stdout
    assert {p.relative_to(repo): p.read_bytes() for p in repo.rglob("*.md")} == before
    assert md.lint_counts(sources, markdownlint)[(".claude/skills/sample/SKILL.md", "MD038")] > 0


def test_uses_merge_base_and_reports_per_file_not_net_totals(repo, capsys):
    (repo / "old.md").write_bytes(b"old")
    commit(repo)
    subprocess.run(["git", "-C", str(repo), "branch", "base"], check=True)
    (repo / "later.md").write_bytes(b"later")
    commit(repo)
    subprocess.run(["git", "-C", str(repo), "switch", "-q", "base"], check=True)
    (repo / "old.md").unlink()
    (repo / "new.md").write_bytes(b"new")
    commit(repo)
    calls = []

    def fake_lint(files, executable):
        calls.append(files)
        return md.Counter({(path, "MD018"): 1 for path in files})

    from unittest.mock import patch
    with patch.object(md, "lint_counts", fake_lint):
        md.compare(repo, "tip", "unused")
    output = capsys.readouterr().out
    assert calls == [{"old.md": b"old"}, {"new.md": b"new"}]
    assert "Warnings: baseline=1, working=1" in output
    assert "-1 MD018 old.md" in output and "+1 MD018 new.md" in output


def test_real_cli_uses_same_default_rules_despite_repo_config(repo, markdownlint):
    (repo / ".markdownlint.json").write_text('{"default": false}', encoding="utf-8")
    assert md.lint_counts({"sample.md": b"#243 literal\n"}, markdownlint)[("sample.md", "MD018")] == 1


def test_markdown_symlink_is_rejected(repo):
    (repo / "target.md").write_bytes(b"# Target\n")
    (repo / "link.md").symlink_to("target.md")
    commit(repo)
    with pytest.raises(ValueError, match="regular Markdown file"):
        md.working_files(repo)
    with pytest.raises(ValueError, match="regular Markdown file"):
        md.baseline_files(repo, "HEAD")


@pytest.mark.parametrize("returncode, stdout, stderr", [
    (2, "", "CLI crash"), (1, "", ""), (0, "", "not JSON"),
    (0, "{}", ""), (0, '[{"fileName":"0.md","ruleNames":["MD018"]}]', ""),
    (1, '[{"fileName":"missing.md","ruleNames":["MD018"]}]', ""),
])
def test_linter_errors_never_look_like_a_clean_report(monkeypatch, returncode, stdout, stderr):
    monkeypatch.setattr(md.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], returncode, stdout, stderr,
    ))
    with pytest.raises(ValueError):
        md.lint_counts({"file.md": b"content"}, "fake-cli")


def test_missing_linter_is_an_error(monkeypatch, capsys):
    monkeypatch.setattr(md.shutil, "which", lambda name: None)
    assert md.main([]) == 2
    assert "markdownlint-cli is required" in capsys.readouterr().err


def test_missing_baseline_is_an_error(repo, markdownlint):
    (repo / "file.md").write_bytes(b"# File\n")
    commit(repo)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--base", "missing-ref"], cwd=repo,
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "markdownlint-diff:" in result.stderr


def test_ignored_untracked_files_are_excluded_and_staged_content_is_not_the_working_copy(repo):
    (repo / ".gitignore").write_text("ignored.md\n", encoding="utf-8")
    (repo / "tracked.md").write_bytes(b"committed")
    commit(repo)
    (repo / "tracked.md").write_bytes(b"staged")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.md"], check=True)
    (repo / "tracked.md").write_bytes(b"working")
    (repo / "ignored.md").write_bytes(b"ignored")
    (repo / "new.md").write_bytes(b"untracked")
    assert md.working_files(repo) == {"tracked.md": b"working", "new.md": b"untracked"}
