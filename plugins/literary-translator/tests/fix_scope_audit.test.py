"""fix_scope_audit.py -- the #607 copy-fidelity check.

Every fixture here builds a REAL durable root the way Step 0a builds one --
copying the plugin's own `assets/scripts/*.py` (minus the never-copied
five), the three workflow templates, `assets/schemas/*.json` and
`assets/languages/*`, then invoking the SHIPPED `scaffold_setup.py` to write
the two bundle markers. Nothing here hand-builds an expected digest or
re-implements the copy pass's membership rule: a fixture that decided for
itself which files "should" be there would agree with a wrong audit by
construction, which is the whole failure mode this script exists to catch.

That the markers are written by `scaffold_setup.py` and verified by
`fix_scope_audit.py`'s own independent plugin-side derivation is the load-
bearing part of `test_clean_tree_verifies`: the two computations start from
different trees (durable vs plugin) and must land on the same sha1.

Both directions are exercised for every verdict. A tamper must go RED, and
the two measured false-RED traps -- a sanctioned `fr.local.json`-style
project-local language override, and the `__pycache__` the interpreter
writes into `scripts/` whenever a durable script imports a durable sibling
-- must stay GREEN. The second is not hypothetical: running `codex_job.py`
from a durable `scripts/` writes `claim_record.cpython-<N>.pyc` there, and
`codex_job.py` runs on every translate and every review dispatch, so an
audit that counted it would fail every batch.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = PLUGIN_ROOT / "skills" / "literary-translator"
ASSETS = SKILL_DIR / "assets"
AUDIT = ASSETS / "scripts" / "fix_scope_audit.py"
SCAFFOLD = ASSETS / "scripts" / "scaffold_setup.py"

# Mirrors the script's own NEVER_COPIED. Restated here on purpose: this file
# is the independent side of that contract, so importing the constant would
# make every "a never-copied script is not reported missing" assertion
# tautological.
NEVER_COPIED = {
    "profile_validate.py",
    "validate_extraction.py",
    "glossary_preflight.py",
    "scaffold_setup.py",
    "fix_scope_audit.py",
}
WORKFLOW_TEMPLATES = (
    "mass-translate-wf.template.js",
    "glossary-pass-wf.template.js",
    "skeptic-pass-wf.template.js",
)


def build_durable_root(tmp_path: Path) -> Path:
    """A Step-0a-shaped durable root, populated by the same copy rule
    SKILL.md's copy paragraph states, then stamped by the SHIPPED marker
    writer."""
    root = tmp_path / "durable"
    for sub in ("scripts", "schemas", "languages", "runs", "segments"):
        (root / sub).mkdir(parents=True)
    for src in (ASSETS / "scripts").glob("*.py"):
        if src.name not in NEVER_COPIED:
            shutil.copy2(src, root / "scripts" / src.name)
    for name in WORKFLOW_TEMPLATES:
        shutil.copy2(ASSETS / "templates" / name, root / "scripts" / name)
    for src in (ASSETS / "schemas").glob("*.json"):
        shutil.copy2(src, root / "schemas" / src.name)
    for src in (ASSETS / "languages").iterdir():
        if src.is_file():
            shutil.copy2(src, root / "languages" / src.name)
    subprocess.run(
        [sys.executable, str(SCAFFOLD), "--durable-root", str(root)],
        check=True, capture_output=True,
    )
    return root


def audit(root: Path):
    proc = subprocess.run(
        [sys.executable, str(AUDIT), "--verify-copies", "--durable-root", str(root)],
        capture_output=True, text=True,
    )
    payload = json.loads(proc.stdout)
    # The exit code is a second, independent statement of the same verdict --
    # a caller that only reads one of them must not be able to disagree with
    # a caller that reads the other.
    assert proc.returncode == (0 if payload["ok"] else 1), (
        f"exit code {proc.returncode} disagrees with ok={payload['ok']}"
    )
    return payload


@pytest.fixture()
def root(tmp_path):
    return build_durable_root(tmp_path)


def test_clean_tree_verifies(root):
    out = audit(root)
    assert out["ok"] is True, out
    # A pass that checked nothing prints exactly like a pass that checked
    # everything, so the count is asserted rather than assumed.
    assert out["n_checked"] > 50, out


def test_content_difference_is_red(root):
    target = root / "scripts" / "validate_draft.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    out = audit(root)
    assert out["ok"] is False
    assert out["differing"] == ["scripts/validate_draft.py"]


def test_missing_copy_is_red(root):
    (root / "scripts" / "draft_ready.py").unlink()
    out = audit(root)
    assert out["ok"] is False
    assert "scripts/draft_ready.py" in out["missing"]


def test_symlink_to_identical_bytes_is_red(root):
    """The case a bytes-only compare would pass. The durable entry is a COPY
    and never a link, and a link's target can be rewritten afterwards through
    a path this script never walks -- so identical content is not enough."""
    target = root / "scripts" / "cache_key.py"
    target.unlink()
    target.symlink_to(ASSETS / "scripts" / "cache_key.py")
    assert target.read_bytes() == (ASSETS / "scripts" / "cache_key.py").read_bytes()
    out = audit(root)
    assert out["ok"] is False
    assert out["irregular"] == ["scripts/cache_key.py"]


def test_unexpected_script_is_red(root):
    (root / "scripts" / "helper.py").write_text("print(1)\n", encoding="utf-8")
    out = audit(root)
    assert out["ok"] is False
    assert out["extra"] == ["helper.py"]


@pytest.mark.parametrize("marker,label", [
    (".plugin_bundle_hash", "plugin_bundle_hash"),
    (".orchestration_bundle_hash", "orchestration_bundle_hash"),
])
def test_tampered_bundle_marker_is_red(root, marker, label):
    """The markers have no plugin twin, but their expected VALUE is derivable
    from the plugin tree, which is why they are covered at all. They matter
    because `cache_key.py` trusts the stored value instead of re-hashing the
    copies -- a rewritten marker makes converged segments classify reusable
    against a plugin they no longer match."""
    (root / "runs" / marker).write_text("deadbeef\n", encoding="utf-8")
    out = audit(root)
    assert out["ok"] is False
    assert out["marker_mismatch"] == [label]


def test_missing_bundle_marker_is_red(root):
    (root / "runs" / ".plugin_bundle_hash").unlink()
    out = audit(root)
    assert out["ok"] is False
    assert "runs/.plugin_bundle_hash" in out["missing"]


def test_project_local_language_override_stays_green(root):
    """SKILL.md's copy paragraph says the pass "never clobbers a project-local
    override coexisting under a different filename (e.g. `fr.local.json`)",
    and SKILL.md walks an operator through creating one. An `extra` sweep over
    languages/ would fire on that legitimate file, which is why the sweep is
    scoped to scripts/."""
    shutil.copy2(ASSETS / "languages" / "fr.json", root / "languages" / "fr.local.json")
    assert audit(root)["ok"] is True


def test_project_local_schema_stays_green(root):
    (root / "schemas" / "project-local.schema.json").write_text("{}", encoding="utf-8")
    assert audit(root)["ok"] is True


def test_pycache_in_scripts_stays_green(root):
    """Reproduced rather than assumed: importing a durable sibling writes here.
    `codex_job.py` imports `claim_record.py` and runs on every translate and
    every review dispatch, so counting this would RED every batch."""
    cache = root / "scripts" / "__pycache__"
    cache.mkdir()
    (cache / "claim_record.cpython-314.pyc").write_bytes(b"\x00\x01")
    assert audit(root)["ok"] is True


def test_running_a_durable_script_really_does_write_pycache(root):
    """The measurement behind the exclusion above, pinned so a future reader
    does not have to take it on faith -- and so that a Python that stopped
    writing bytecode here would show up as a changed fact rather than as a
    silently pointless exclusion."""
    subprocess.run(
        [sys.executable, str(root / "scripts" / "codex_job.py"), "--help"],
        capture_output=True, cwd=str(root / "scripts"),
    )
    produced = list((root / "scripts" / "__pycache__").glob("claim_record.*.pyc"))
    assert produced, "expected the interpreter to cache the imported durable sibling"
    assert audit(root)["ok"] is True


def test_never_copied_scripts_are_not_reported_missing(root):
    """They are absent from every durable root by design; reporting them would
    make a correct tree RED forever."""
    out = audit(root)
    assert out["ok"] is True
    for name in NEVER_COPIED:
        assert not (root / "scripts" / name).exists()


def test_workflow_templates_are_compared(root):
    """They live under assets/templates/ but are copied into scripts/, and two
    of them are PLUGIN_BUNDLE_MEMBERS -- a compared set built only from
    assets/scripts/ would silently skip the file this whole release edits."""
    target = root / "scripts" / "mass-translate-wf.template.js"
    target.write_text(target.read_text(encoding="utf-8") + "\n// tampered\n", encoding="utf-8")
    out = audit(root)
    assert out["ok"] is False
    assert "scripts/mass-translate-wf.template.js" in out["differing"]


def test_refuses_when_run_from_inside_the_audited_root(tmp_path, root):
    """The script is never copied into a durable root precisely so this cannot
    happen; the guard exists because a checker inside the tree it audits would
    compare that tree against itself and report clean."""
    fake_assets = root / "vendored" / "assets"
    (fake_assets / "scripts").mkdir(parents=True)
    shutil.copy2(AUDIT, fake_assets / "scripts" / "fix_scope_audit.py")
    proc = subprocess.run(
        [sys.executable, str(fake_assets / "scripts" / "fix_scope_audit.py"),
         "--verify-copies", "--durable-root", str(root)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["verdict"] == "error"
    assert "inside the durable root" in payload["error"]


def test_absent_durable_root_fails_with_a_json_line(tmp_path):
    """A relay agent that gets a traceback instead of a JSON line cannot
    report anything, and the workflow would read the call as infrastructure
    failure rather than as a verdict."""
    proc = subprocess.run(
        [sys.executable, str(AUDIT), "--verify-copies",
         "--durable-root", str(tmp_path / "nope")],
        capture_output=True, text=True,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False and payload["verdict"] == "error"


def test_mismatch_carries_the_upgrade_reading_not_just_tampering(root):
    """A plugin upgraded mid-project produces the identical signal, and the
    script cannot tell the two apart. Overstating it as tampering in the one
    text an operator actually reads would be the same over-claim this release
    spent four review rounds removing from its own prose."""
    (root / "scripts" / "draft_sha1.py").write_text("# x\n", encoding="utf-8")
    out = audit(root)
    assert out["ok"] is False
    assert "not by itself proof of tampering" in out["remedy"].lower() or \
           "NOT by itself proof of tampering" in out["remedy"]
    assert "Step 0a" in out["remedy"]
