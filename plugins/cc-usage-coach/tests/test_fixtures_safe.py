"""
Positive-allowlist safety gate over EVERY file under tests/fixtures/.

This ships publicly, so the fixtures must contain NO real personal strings — only the
synthetic neutral shapes the corpus builder emits. The gate is an ALLOWLIST (assert the
data matches the synthetic shape), not a denylist of known-bad tokens, so a future fixture
edit that introduces a real path/label fails loudly here. Keep this file free of any real
name — it must reference only the synthetic allowlist below.

Run: python3 -m pytest tests/test_fixtures_safe.py -q
"""
import os, re, glob, json

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")

# Synthetic shapes the corpus builder is allowed to emit.
ALLOWED_PROJECTS = {"project-a", "project-b", "project-c", "subagents"}
WORKFLOW_LABEL = re.compile(r"^wf_[0-9a-z]+$")
# Every absolute home path in the fixtures must be the synthetic user `x`.
USERS_OCCURRENCE = re.compile(r"/Users/([^/\"]+)")
# Generic mangled / URL-encoded CC path forms that must NEVER appear in synthetic fixtures.
# (Checks for any real, personal config-dir names live ONLY in the private pre-release audit —
# embedding such names in this public test would itself leak them.)
FORBIDDEN_SUBSTRINGS = ("%2F", "-Users-", "-home-", "/home/")


def _fixture_files():
    files = sorted(glob.glob(os.path.join(FIXTURES, "**", "*"), recursive=True))
    return [f for f in files if os.path.isfile(f)]


def test_fixtures_exist():
    names = {os.path.basename(f) for f in _fixture_files()}
    assert {"sessions.jsonl", "turns.jsonl", "tools.json", "golden_pack.json"} <= names


def test_every_users_path_is_synthetic_x():
    """Every `/Users/<name>` occurrence in any fixture file is `/Users/x/` (synthetic)."""
    for f in _fixture_files():
        txt = open(f, encoding="utf-8").read()
        for m in USERS_OCCURRENCE.finditer(txt):
            assert m.group(1) == "x", f"non-synthetic /Users/ path in {os.path.basename(f)}: /Users/{m.group(1)}"


def test_no_forbidden_substrings_anywhere():
    for f in _fixture_files():
        txt = open(f, encoding="utf-8").read()
        for bad in FORBIDDEN_SUBSTRINGS:
            assert bad not in txt, f"forbidden token {bad!r} present in {os.path.basename(f)}"


def test_project_labels_are_allowlisted():
    """Every session `p` (project label) is in the allowlist or a synthetic wf_ workflow id."""
    seen = set()
    with open(os.path.join(FIXTURES, "frozen_dataset", "sessions.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                seen.add(json.loads(line)["p"])
    for label in seen:
        ok = label in ALLOWED_PROJECTS or bool(WORKFLOW_LABEL.match(label))
        assert ok, f"project label not in synthetic allowlist: {label!r}"
    # The allowlisted labels must actually be exercised (corpus didn't silently drop coverage).
    assert {"project-a", "project-b", "project-c"} <= seen


def test_golden_pack_has_no_path_or_source_leak():
    """The shareable golden pack must carry no filesystem path or source field."""
    blob = open(os.path.join(FIXTURES, "golden_pack.json"), encoding="utf-8").read()
    for needle in ("/Users/", "/home/", ".jsonl", "source_path"):
        assert needle not in blob, f"path/source leak in golden_pack.json: {needle!r}"
