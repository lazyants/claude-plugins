"""Tests for `adapter_check.py` (plan section 6): `run` exercises the "toy"
fixture adapter (`tests/fixtures/toy_adapter.py`) against a copy of
`tests/fixtures/toy_project/` in a temporary project copy, and `accept`
turns a coverage turn's answer into `adapter.lock.json`.

`_make_workspace` (and `_run`) are reused by `test_collect.py`.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import adapter_check
import lz_common

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
SCRIPTS_DIR = TESTS_DIR.parent / "skills" / "software-localizer" / "scripts"
ADAPTER_CHECK = SCRIPTS_DIR / "adapter_check.py"
COLLECT = SCRIPTS_DIR / "collect.py"
TOY_PROJECT = FIXTURES_DIR / "toy_project"
TOY_ADAPTER = FIXTURES_DIR / "toy_adapter.py"

# Mirrors the toy adapter's expected --options shape (references/adapter-contract.md,
# plan section 5.4): labels for the two source plural-form counts the fixture project
# uses (2: cart.itemCount, 3: mail.unreadCount).
TOY_OPTIONS = {
    "plural_labels": {
        "en": {"2": [["one", True], ["other", False]], "3": [["zero", True], ["one", True], ["other", False]]},
        "de": {"2": [["one", True], ["other", False]], "3": [["one", True], ["other", False]]},
        "ru": {
            "2": [["one", False], ["few", False], ["many", False]],
            "3": [["one", False], ["few", False], ["many", False]],
        },
    },
    "general": {"2": 1, "3": 2},
}


def _make_workspace(work_root: Path, *, project_git: bool = False, options_extra: dict | None = None):
    """`<work_root>/R` (the workspace, with the toy adapter under
    `R/adapter/adapter.py`) and a fresh copy of the toy project fixture in
    its own OS-temp directory, with a valid `localize.json` already
    written. Returns `(root, project_dir, cfg)`.

    The project copy deliberately does NOT live under `work_root` (which
    sits inside `tests/.work/` -- gitignored, but still physically inside
    THIS repo's own git work tree): `adapter_check.py`'s coverage inventory
    detects an ENCLOSING git work tree, not just a local `.git`, so a
    project nested under `work_root` would register as "inside" this
    checkout's own work tree and get an empty git-based inventory
    (everything under `tests/.work/` is excluded) instead of the plain
    walk a project with no git of its own is meant to fall back to."""
    root = work_root / "R"
    root.mkdir()
    # .resolve(): macOS's temp dir is reached through a /var -> /private/var
    # symlink; lz_common.load_config resolves project_root the same way, so
    # this must match or a straight string comparison against cfg["project_root"]
    # would spuriously differ only by that symlink hop.
    project_dir = Path(tempfile.mkdtemp(prefix="lz-swloc-project-")).resolve()
    atexit.register(shutil.rmtree, project_dir, ignore_errors=True)
    shutil.copytree(TOY_PROJECT, project_dir, dirs_exist_ok=True)

    adapter_dir = root / "adapter"
    adapter_dir.mkdir()
    shutil.copy2(TOY_ADAPTER, adapter_dir / "adapter.py")

    options = json.loads(json.dumps(TOY_OPTIONS))
    if options_extra:
        options.update(options_extra)

    cfg = {
        "schema": 1,
        "project_root": str(project_dir),
        "source_locale": "en",
        "target_locales": ["de", "ru"],
        "adapter": {"argv": [sys.executable, "adapter/adapter.py"], "options": options},
        "style": {
            "de": {"formality": "Sie", "notes": "Formal, concise."},
            "ru": {"formality": "вы", "notes": "Polite form."},
        },
        "allow_identical": [],
        "batch_size": 40,
        "max_rounds": 3,
        "adapter_timeout_s": 30,
    }
    lz_common.atomic_write_json(root / "localize.json", cfg)

    if project_git:
        subprocess.run(["git", "init", "-q"], cwd=str(project_dir), check=True, timeout=30)

    return root, project_dir, cfg


def _run(script: Path, args: list[str], cwd: Path | None = None) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(cwd) if cwd is not None else None,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected one JSON line on stdout, got {proc.stdout!r} (stderr: {proc.stderr!r})"
    return proc.returncode, json.loads(lines[0])


def _write_coverage(path: Path, missing: list[dict]) -> None:
    path.write_text(json.dumps({"missing": missing}), encoding="utf-8")


# --- run(): the valid fixture ------------------------------------------------


def test_run_passes_on_the_unmodified_fixture(work_root):
    root, project_dir, cfg = _make_workspace(work_root)

    code, reply = _run(ADAPTER_CHECK, ["run", "--root", str(root)])

    assert code == 0
    assert reply["ok"] is True
    result = reply["result"]
    assert result["staging_consistent"] == {"ok": True}
    assert result["unchanged_round_trip"]["ok"] is True
    assert result["unchanged_round_trip"]["diffs"] == []
    assert result["awkward_round_trip"]["ok"] is True
    assert result["parse_sanity"]["ok"] is True
    assert result["parse_sanity"]["source_failures"] == []

    persisted = json.loads((root / "runs" / "_adapter_check.json").read_text(encoding="utf-8"))
    assert persisted["ok"] is True

    packet = json.loads((root / "runs" / "_coverage" / "packet.json").read_text(encoding="utf-8"))
    assert packet["project_root"] == str(project_dir)
    files_by_path = {entry["file"]: entry["ids"] for entry in packet["files"]}
    assert set(files_by_path["locales/en.json"]) == {
        "app.title", "nav.home", "nav.about", "nav.contact", "nav.save", "dialog.confirm",
        "user.greeting", "notice.leadingSpace", "cart.itemCount", "mail.unreadCount",
        "footer.copyright", "error.notFound",
    }
    assert "locales/en.json" in packet["inventory"]
    assert "locales/de.json" in packet["inventory"]

    prompt = (root / "runs" / "_coverage" / "prompt.md").read_text(encoding="utf-8")
    assert "{{PACKET_JSON}}" not in prompt
    assert '"app.title"' in prompt


def test_run_is_idempotent(work_root):
    root, project_dir, cfg = _make_workspace(work_root)
    _run(ADAPTER_CHECK, ["run", "--root", str(root)])
    code, reply = _run(ADAPTER_CHECK, ["run", "--root", str(root)])
    assert code == 0
    assert reply["ok"] is True


def test_run_accepts_a_relative_root_from_a_different_cwd(work_root):
    # A relative --root used to stay relative all the way into the adapter
    # subprocess's argv, which runs with cwd=project_dir -- a different
    # directory than wherever the relative root string was valid from. The
    # OS then resolved the script path against the wrong directory and the
    # adapter subprocess could not be found at all.
    #
    # `elsewhere` is a sibling of R (one ".." level) rather than some
    # unrelated deeply-nested tmp directory: with enough ".." segments a
    # broken relative path can walk past the filesystem root and coincide
    # with the right absolute path by pure depth accident, masking the bug
    # this test exists to catch.
    root, project_dir, cfg = _make_workspace(work_root)
    elsewhere = work_root / "elsewhere"
    elsewhere.mkdir()
    rel_root = os.path.relpath(root, start=elsewhere)
    assert rel_root == "../R"

    code, reply = _run(ADAPTER_CHECK, ["run", "--root", rel_root], cwd=elsewhere)

    assert code == 0, reply
    assert reply["ok"] is True


# --- run(): a lossy adapter must fail -----------------------------------------


def test_run_fails_when_export_trims_values(work_root):
    """A lossy adapter that strips surrounding whitespace breaks the
    unchanged round trip on `notice.leadingSpace`, which has both."""
    root, project_dir, cfg = _make_workspace(work_root, options_extra={"lossy": "trim"})

    code, reply = _run(ADAPTER_CHECK, ["run", "--root", str(root)])

    assert code == 1
    assert reply["ok"] is False
    assert reply["result"]["unchanged_round_trip"]["ok"] is False
    assert reply["result"]["unchanged_round_trip"]["diffs"]


def test_run_fails_when_export_drops_a_key(work_root):
    """A lossy adapter that silently skips one id breaks the awkward round
    trip once that id is the one given an awkward value."""
    root, project_dir, cfg = _make_workspace(
        work_root, options_extra={"lossy": "drop_key", "lossy_drop_key": "app.title"}
    )

    code, reply = _run(ADAPTER_CHECK, ["run", "--root", str(root)])

    assert code == 1
    assert reply["ok"] is False
    # The dropped key is unchanged data during the unchanged round trip (skipping a
    # write that would not have changed anything is invisible), so only the awkward
    # round trip -- which gives app.title a genuinely new value -- catches it.
    assert reply["result"]["unchanged_round_trip"]["ok"] is True
    assert reply["result"]["awkward_round_trip"]["ok"] is False


def test_run_fails_when_the_adapter_reads_an_unlisted_file(work_root):
    """`collect` against the live project can see a file the adapter never
    declares in `files`; staged with only the declared files, that file is
    gone and the collect result differs -- the check this flow exists for."""
    root, project_dir, cfg = _make_workspace(
        work_root, options_extra={"unlisted_extra_file": "meta/extra.txt"}
    )
    (project_dir / "meta").mkdir()
    (project_dir / "meta" / "extra.txt").write_text("An extra string the adapter never declared.\n", encoding="utf-8")

    code, reply = _run(ADAPTER_CHECK, ["run", "--root", str(root)])

    assert code == 1
    assert reply["ok"] is False
    staging = reply["result"]["staging_consistent"]
    assert staging["ok"] is False
    assert staging["only_in_live"] == ["unlisted.extra"]
    assert staging["only_in_staged"] == []


def test_run_fails_when_a_source_form_does_not_parse(work_root):
    root, project_dir, cfg = _make_workspace(work_root)
    en_path = project_dir / "locales" / "en.json"
    data = json.loads(en_path.read_text(encoding="utf-8"))
    data["error.notFound"] = "Stray @ token breaks this"
    en_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    code, reply = _run(ADAPTER_CHECK, ["run", "--root", str(root)])

    assert code == 1
    assert reply["ok"] is False
    assert reply["result"]["parse_sanity"]["ok"] is False
    assert any("error.notFound" in key for key in reply["result"]["parse_sanity"]["source_failures"])


def test_run_reports_a_source_failure_a_colliding_key_used_to_hide(work_root):
    # Old code built parse-sanity keys by string concatenation: a non-plural
    # id "cart.itemCount#0" and the first form of the plural id
    # "cart.itemCount" both built the key "src::cart.itemCount#0". Whichever
    # item the adapter processed last overwrote the other's result in the
    # results dict -- here the plural's valid form 0 (processed after,
    # since it appears later in en.json) silently overwrote the failing
    # non-plural's result, so the check passed when it should not have.
    root, project_dir, cfg = _make_workspace(work_root)
    en_path = project_dir / "locales" / "en.json"
    data = json.loads(en_path.read_text(encoding="utf-8"))
    new_data = {}
    for key, value in data.items():
        if key == "cart.itemCount":
            new_data["cart.itemCount#0"] = "Stray @ token breaks this"
        new_data[key] = value
    en_path.write_text(json.dumps(new_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    code, reply = _run(ADAPTER_CHECK, ["run", "--root", str(root)])

    assert code == 1
    assert reply["ok"] is False
    result = reply["result"]["parse_sanity"]
    assert result["ok"] is False
    assert "cart.itemCount#0" in result["source_failures"]
    assert "cart.itemCount" not in result["source_failures"]


# --- run(): the independent project inventory ---------------------------------


def test_inventory_includes_untracked_and_excludes_gitignored(work_root):
    root, project_dir, cfg = _make_workspace(work_root, project_git=True)
    (project_dir / ".gitignore").write_text("ignored_secret.txt\n", encoding="utf-8")
    (project_dir / "ignored_secret.txt").write_text("nope", encoding="utf-8")
    (project_dir / "extra_catalog.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=str(project_dir), check=True, timeout=30)

    code, reply = _run(ADAPTER_CHECK, ["run", "--root", str(root)])
    assert code == 0

    packet = json.loads((root / "runs" / "_coverage" / "packet.json").read_text(encoding="utf-8"))
    assert "extra_catalog.json" in packet["inventory"]
    assert "ignored_secret.txt" not in packet["inventory"]
    assert "locales/en.json" in packet["inventory"]


def test_inventory_detects_an_enclosing_git_worktree_without_a_local_git(work_root, tmp_path):
    # The project itself has no `.git` -- it is a subdirectory of a larger
    # checkout (a monorepo layout). Detecting only a LOCAL `.git` fell
    # through to the unfiltered directory walk, which ignores .gitignore
    # entirely: a large ignored tree could crowd the coverage packet.
    root, project_dir, cfg = _make_workspace(work_root)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo_root), check=True, timeout=30)

    nested_project = repo_root / "nested" / "project"
    nested_project.parent.mkdir(parents=True)
    shutil.move(str(project_dir), str(nested_project))
    assert not (nested_project / ".git").exists()

    (repo_root / ".gitignore").write_text("nested/project/ignored_secret.txt\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=str(repo_root), check=True, timeout=30)
    (nested_project / "ignored_secret.txt").write_text("nope", encoding="utf-8")
    (nested_project / "extra_catalog.json").write_text("{}\n", encoding="utf-8")

    cfg["project_root"] = str(nested_project)
    lz_common.atomic_write_json(root / "localize.json", cfg)

    code, reply = _run(ADAPTER_CHECK, ["run", "--root", str(root)])
    assert code == 0, reply

    packet = json.loads((root / "runs" / "_coverage" / "packet.json").read_text(encoding="utf-8"))
    assert "extra_catalog.json" in packet["inventory"]
    assert "ignored_secret.txt" not in packet["inventory"]
    assert "locales/en.json" in packet["inventory"]


# --- coverage prompt: no built-in fallback --------------------------------------


def test_write_coverage_prompt_fails_cannot_when_packaged_template_missing(work_root, capsys):
    # No fallback template: a missing packaged asset is a broken install, not
    # something to silently paper over with a second, drifting prompt.
    fake_plugin_root = work_root / "fake_plugin"
    (fake_plugin_root / "assets" / "templates").mkdir(parents=True)
    out_dir = work_root / "coverage_out"

    with pytest.raises(SystemExit) as exc_info:
        adapter_check._write_coverage_prompt(fake_plugin_root, out_dir, {"schema": 1})

    assert exc_info.value.code == lz_common.EXIT_CANNOT
    payload = json.loads(capsys.readouterr().out.splitlines()[0])
    assert payload["ok"] is False
    assert "coverage_TASK.md" in payload["error"]
    assert not (out_dir / "prompt.md").exists()


# --- accept() ------------------------------------------------------------------


def test_accept_succeeds_with_no_missing_strings(work_root):
    root, project_dir, cfg = _make_workspace(work_root)
    run_code, _ = _run(ADAPTER_CHECK, ["run", "--root", str(root)])
    assert run_code == 0

    coverage_path = work_root / "coverage.json"
    _write_coverage(coverage_path, [])

    code, reply = _run(
        ADAPTER_CHECK, ["accept", "--root", str(root), "--coverage", str(coverage_path), "--by", "tester"]
    )

    assert code == 0
    assert reply["ok"] is True
    lock = json.loads((root / "adapter.lock.json").read_text(encoding="utf-8"))
    assert lock["by"] == "tester"
    assert lock["missing"] == []
    assert lock["out_of_scope"] == []
    assert lock["files"]
    assert lock["options_sha256"]

    # A fresh load_config + require_accepted_adapter must now be satisfied.
    fresh_cfg = lz_common.load_config(root)
    lz_common.require_accepted_adapter(root, fresh_cfg)  # does not raise


def test_accept_refuses_an_unaccepted_missing_string(work_root):
    root, project_dir, cfg = _make_workspace(work_root)
    _run(ADAPTER_CHECK, ["run", "--root", str(root)])

    coverage_path = work_root / "coverage.json"
    _write_coverage(
        coverage_path,
        [{"file": "locales/en.json", "key": "brand.name", "why_user_visible": "shown in the footer"}],
    )

    code, reply = _run(
        ADAPTER_CHECK, ["accept", "--root", str(root), "--coverage", str(coverage_path), "--by", "tester"]
    )

    assert code == 1
    assert reply["ok"] is False
    assert reply["unaccepted"] == [
        {"file": "locales/en.json", "key": "brand.name", "why_user_visible": "shown in the footer"}
    ]
    assert not (root / "adapter.lock.json").exists()


def test_accept_allows_a_missing_string_marked_out_of_scope(work_root):
    root, project_dir, cfg = _make_workspace(work_root)
    _run(ADAPTER_CHECK, ["run", "--root", str(root)])

    coverage_path = work_root / "coverage.json"
    _write_coverage(
        coverage_path,
        [{"file": "locales/en.json", "key": "brand.name", "why_user_visible": "shown in the footer"}],
    )

    code, reply = _run(
        ADAPTER_CHECK,
        [
            "accept", "--root", str(root), "--coverage", str(coverage_path), "--by", "tester",
            "--out-of-scope", "locales/en.json#brand.name",
        ],
    )

    assert code == 0
    assert reply["ok"] is True
    lock = json.loads((root / "adapter.lock.json").read_text(encoding="utf-8"))
    assert lock["out_of_scope"] == ["locales/en.json#brand.name"]
    assert lock["missing"][0]["key"] == "brand.name"


def test_accept_out_of_scope_key_for_a_whole_missed_file(work_root):
    root, project_dir, cfg = _make_workspace(work_root)
    _run(ADAPTER_CHECK, ["run", "--root", str(root)])

    coverage_path = work_root / "coverage.json"
    _write_coverage(
        coverage_path, [{"file": "docs/help.md", "key": None, "why_user_visible": "a whole catalog the adapter misses"}]
    )

    code, reply = _run(
        ADAPTER_CHECK,
        [
            "accept", "--root", str(root), "--coverage", str(coverage_path), "--by", "tester",
            "--out-of-scope", "docs/help.md",
        ],
    )

    assert code == 0
    assert reply["ok"] is True


def test_accept_refuses_when_adapter_changed_since_run(work_root):
    """`run` binds its stored digest to what it actually exercised; editing
    the adapter afterward must not let `accept` lock in an unexercised
    version."""
    root, project_dir, cfg = _make_workspace(work_root)
    run_code, _ = _run(ADAPTER_CHECK, ["run", "--root", str(root)])
    assert run_code == 0

    adapter_file = root / "adapter" / "adapter.py"
    adapter_file.write_text(adapter_file.read_text(encoding="utf-8") + "\n# mutated after run\n", encoding="utf-8")

    coverage_path = work_root / "coverage.json"
    _write_coverage(coverage_path, [])

    code, reply = _run(
        ADAPTER_CHECK, ["accept", "--root", str(root), "--coverage", str(coverage_path), "--by", "tester"]
    )

    assert code == 1
    assert reply["ok"] is False
    assert "adapter_check.py run" in reply["error"]
    assert not (root / "adapter.lock.json").exists()


def test_accept_requires_a_prior_run(work_root):
    root, project_dir, cfg = _make_workspace(work_root)
    coverage_path = work_root / "coverage.json"
    _write_coverage(coverage_path, [])

    code, reply = _run(
        ADAPTER_CHECK, ["accept", "--root", str(root), "--coverage", str(coverage_path), "--by", "tester"]
    )

    assert code == lz_common.EXIT_CANNOT
    assert reply["ok"] is False


def test_accept_refuses_when_run_did_not_pass(work_root):
    root, project_dir, cfg = _make_workspace(work_root, options_extra={"lossy": "trim"})
    run_code, _ = _run(ADAPTER_CHECK, ["run", "--root", str(root)])
    assert run_code == 1

    coverage_path = work_root / "coverage.json"
    _write_coverage(coverage_path, [])

    code, reply = _run(
        ADAPTER_CHECK, ["accept", "--root", str(root), "--coverage", str(coverage_path), "--by", "tester"]
    )

    assert code == 1
    assert reply["ok"] is False
    assert not (root / "adapter.lock.json").exists()


# --- the lock refuses a changed adapter file or changed options ----------------


def test_lock_refuses_a_changed_adapter_file(work_root):
    root, project_dir, cfg = _make_workspace(work_root)
    _run(ADAPTER_CHECK, ["run", "--root", str(root)])
    coverage_path = work_root / "coverage.json"
    _write_coverage(coverage_path, [])
    accept_code, _ = _run(
        ADAPTER_CHECK, ["accept", "--root", str(root), "--coverage", str(coverage_path), "--by", "tester"]
    )
    assert accept_code == 0

    fresh_cfg = lz_common.load_config(root)
    lz_common.require_accepted_adapter(root, fresh_cfg)  # accepted: does not raise

    adapter_file = root / "adapter" / "adapter.py"
    adapter_file.write_text(adapter_file.read_text(encoding="utf-8") + "\n# mutated\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        lz_common.require_accepted_adapter(root, fresh_cfg)
    assert exc_info.value.code == lz_common.EXIT_FAIL


def test_lock_refuses_changed_options(work_root):
    root, project_dir, cfg = _make_workspace(work_root)
    _run(ADAPTER_CHECK, ["run", "--root", str(root)])
    coverage_path = work_root / "coverage.json"
    _write_coverage(coverage_path, [])
    accept_code, _ = _run(
        ADAPTER_CHECK, ["accept", "--root", str(root), "--coverage", str(coverage_path), "--by", "tester"]
    )
    assert accept_code == 0

    localize_path = root / "localize.json"
    mutated_cfg = json.loads(localize_path.read_text(encoding="utf-8"))
    mutated_cfg["adapter"]["options"]["extra_flag"] = True
    lz_common.atomic_write_json(localize_path, mutated_cfg)
    fresh_cfg = lz_common.load_config(root)

    with pytest.raises(SystemExit) as exc_info:
        lz_common.require_accepted_adapter(root, fresh_cfg)
    assert exc_info.value.code == lz_common.EXIT_FAIL
