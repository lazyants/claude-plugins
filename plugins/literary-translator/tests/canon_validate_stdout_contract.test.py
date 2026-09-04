#!/usr/bin/env python3
"""The stream `canon_validate.py` reports a verdict on (#851).

CHARACTERISATION, not a regression test: it passes before and after the fix it
was written for. `glossary_dispatch_driver.py` falls back to a failed command's
STDOUT when its stderr is empty, and that fallback is only correct while the
verdict really does arrive on stdout. Pinning it here means a future move to
stderr says so out loud instead of quietly turning the fallback into dead code.

WHY ITS OWN FILE rather than a case in `glossary_dispatch_driver.test.py`, where
the rest of #851's tests live. That module copies the DRIVER into a tmp dir with
`shutil.copy2` + `spec_from_file_location` to exercise a deployed layout. Naming
`canon_validate.py` in the same file makes `senses_fixture_guard.test.py` read
those pre-existing idioms as an unstaged isolation of a canon_senses CONSUMER --
a file-level match it documents as deliberately imprecise. Silencing that with an
`AUTHORITATIVE_FIXTURE_INVENTORY` entry would exempt the whole driver module from
the guard, including any future real violation in it. A separate file with no
copy or loader idiom keeps the guard fully armed where it matters.

No staging is needed here: the script runs from its own real location, where
`canon_senses.py` is already the sibling it expects.
"""

import json
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CANON_VALIDATE = (PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
                  / "scripts" / "canon_validate.py")


def test_a_refusal_is_reported_on_stdout_with_stderr_left_empty(tmp_path):
    """A refusal -- ANY refusal -- goes to stdout. This invocation exits 1 on
    per-item schema validation, before `--approve-to` is ever consulted, and the
    reason still arrives on stdout with stderr holding nothing at all."""
    fragment = tmp_path / "fragment.json"
    fragment.write_text(json.dumps(
        [{"source_form": "name0", "canonical_target_form": "N0",
          "basis": "established"}]))
    proc = subprocess.run(
        [sys.executable, str(CANON_VALIDATE),
         "--canon", str(tmp_path / "canon.json"), "--research-mode", "live",
         "--check-batch", str(fragment),
         "--approve-to", str(tmp_path / "approved.json")],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1, proc.stderr
    assert proc.stderr == "", "canon_validate.py now writes stderr; see #851"
    assert '"error"' in proc.stdout


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
