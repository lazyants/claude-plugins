# `backlink_e2e` fixture

The committed acceptance fixture for `tests/backlink_integrity_e2e.test.py`
(RFC lt-appendix-backlink-integrity, 1.8.0). See `case_spec.py`'s own
module docstring for what each of the 12 canon entries in this fixture
isolates.

## Files

- `case_spec.py` -- the hand-authored manifest/segpack/draft content (pure
  data + small derivations). The test file expands this into a full
  `durable_root` at test time via copies of `tests/assemble.test.py`'s own
  `write_manifest`/`write_segpack`/`write_draft`/`write_ledger` helpers.
- `canon.json`, `canon_senses.json` -- committed as-is (no placeholders).
- `languages/backlink_e2e.json` -- the particle config (a project-local
  preset, not one of the shipped Latin/Hebrew presets).
- `profile.yml` -- committed with two literal placeholders
  (`PLACEHOLDER_DURABLE_ROOT`, `PLACEHOLDER_DESTINATION`,
  `PLACEHOLDER_SOURCE_PATH`) the test's `stage_fixture()` overwrites with
  the real, absolute `tmp_path`-derived paths before writing it into the
  staged project. `output.adapter_config.obsidian.mentions_section.enabled`
  is likewise toggled per run (flag-on vs. flag-off).
- `source_stub.txt` -- exists only so `profile.yml`'s `source.path`
  resolves to a real, readable file for `profile_validate.py`'s own
  existence check; never read by `assemble.py` itself (the fixture is
  staged directly at the manifest/segpack/draft layer, bypassing
  extraction entirely).
- `expected_vault/` -- the golden, flag-ON rendered vault
  (`tests/backlink_integrity_e2e.test.py::
  test_flag_on_end_to_end_matches_expected_vault_and_gate_report` compares
  the REAL pipeline's output against this byte-for-byte via
  `diff_rendered_output.compare(reduce_vault(...), reduce_vault(...))`).
  Hidden entries (`.assembled/`, the `.literary-translator-vault.json`
  ownership marker) are deliberately NOT committed here --
  `list_vault_relpaths` skips every hidden path, so they never participate
  in the comparison and would only be committed noise.

## Regenerating `expected_vault/`

Only regenerate this after a REVIEWED, intentional change to the
renderer's output shape (a `render_obsidian.py`/`assemble.py` behavior
change this fixture is meant to pin) -- never to make a failing test pass
without first understanding why the rendered output changed.

```python
import importlib.util, shutil
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "e2e_test_mod", "tests/backlink_integrity_e2e.test.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

tmp_path = Path("/tmp/backlink_e2e_regen")
shutil.rmtree(tmp_path, ignore_errors=True)
tmp_path.mkdir(parents=True)

root, proc, nodestream = mod.run_flag_pipeline(tmp_path, True, "regen")
assert proc.returncode == 0, proc.stdout + proc.stderr

expected = Path("tests/fixtures/backlink_e2e/expected_vault")
shutil.rmtree(expected, ignore_errors=True)
shutil.copytree(root / "out", expected)
shutil.rmtree(expected / ".assembled", ignore_errors=True)
(expected / ".literary-translator-vault.json").unlink(missing_ok=True)
```

Then **review the diff** (`git diff tests/fixtures/backlink_e2e/expected_vault/`)
before committing -- every changed line must be explainable by the
intentional change, never a surprise.
