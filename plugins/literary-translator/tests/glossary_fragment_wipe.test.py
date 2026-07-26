"""resume_setup.py wipes stale glossary fragments before a run so a later wait
step cannot poll a prior run's out_{i}_attempt_{n}.json (or a stale approved_*
snapshot) while assuming it is absent (LT 1.16.0, Facet A of "bind the merge to
the bytes that were reviewed"). The rule is conditioned on `resume`:

  fresh (resume False): wipe ALL out_* and approved_* attempts, incl. attempt 0
  resume (resume True): keep out_{i}_attempt_0, wipe out_* n>=1 and ALL approved_*

Nothing else deletes fragments and a MATCH-resume reuses the same run_id, so on
a fresh-id collision with an orphaned glossary dir a surviving attempt 0 would
be audited stale -- which is why a fresh run keeps nothing.
"""

import importlib.util
from pathlib import Path

import pytest

_RS_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills/literary-translator/assets/scripts/resume_setup.py"
)


@pytest.fixture(scope="module")
def rs():
    spec = importlib.util.spec_from_file_location("resume_setup_under_test", _RS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(dirpath: Path) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    for name in (
        "out_0_attempt_0.json",
        "out_0_attempt_1.json",
        "out_1_attempt_0.json",
        "out_2_attempt_3.json",
        "approved_0_attempt_0.json",
        "approved_1_attempt_2.json",
        # non-fragment files that must never be touched:
        "manifest_0.json",
        "manifest_all.json",
        "input.digest",
    ):
        (dirpath / name).write_text("x", encoding="utf-8")


def _names(dirpath: Path):
    return sorted(p.name for p in dirpath.iterdir())


def test_fresh_run_wipes_every_attempt_including_zero(rs, tmp_path):
    d = tmp_path / "glossary" / "runs" / "R"
    _seed(d)
    rs._wipe_stale_glossary_fragments(d, resume=False)
    assert _names(d) == ["input.digest", "manifest_0.json", "manifest_all.json"], (
        "a fresh run must trust nothing on disk -- every out_* and approved_* "
        f"attempt, including attempt 0, must go. Survivors: {_names(d)}"
    )


def test_resume_keeps_attempt_zero_but_wipes_the_rest(rs, tmp_path):
    d = tmp_path / "glossary" / "runs" / "R"
    _seed(d)
    rs._wipe_stale_glossary_fragments(d, resume=True)
    assert _names(d) == [
        "input.digest",
        "manifest_0.json",
        "manifest_all.json",
        "out_0_attempt_0.json",
        "out_1_attempt_0.json",
    ], (
        "a resume keeps out_{i}_attempt_0 (the resume-skip optimisation depends "
        "on it and it is still citation-reviewed) but must wipe every attempt "
        f">=1 and every approved_* snapshot. Survivors: {_names(d)}"
    )


def test_wipe_is_a_noop_on_an_empty_dir(rs, tmp_path):
    d = tmp_path / "glossary" / "runs" / "R"
    d.mkdir(parents=True)
    rs._wipe_stale_glossary_fragments(d, resume=False)  # must not raise
    assert _names(d) == []


def test_write_run_dir_glossary_passes_the_real_resume_flag(rs, tmp_path, monkeypatch):
    """Wiring: write_run_dir's glossary branch must invoke the wipe with the
    run's actual resume flag and the run-scoped glossary dir -- a spy proves the
    call without depending on manifest writing."""
    monkeypatch.setattr(rs, "DURABLE_ROOT", tmp_path)
    monkeypatch.setattr(rs, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(rs, "write_glossary_manifests", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(
        rs, "_wipe_stale_glossary_fragments", lambda d, resume: calls.append((d, resume))
    )
    (tmp_path / "runs").mkdir()

    run_id = "20260101T000000Z"
    rs.write_run_dir(run_id, resume=True, input_digest="d", kind="glossary", payload={"batches": {}})
    assert calls == [(tmp_path / "glossary" / "runs" / run_id, True)]


def test_non_glossary_run_does_not_wipe(rs, tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "DURABLE_ROOT", tmp_path)
    monkeypatch.setattr(rs, "RUNS_DIR", tmp_path / "runs")
    calls = []
    monkeypatch.setattr(
        rs, "_wipe_stale_glossary_fragments", lambda d, resume: calls.append((d, resume))
    )
    (tmp_path / "runs").mkdir()
    rs.write_run_dir("20260101T000000Z", resume=False, input_digest="d", kind="mass", payload={})
    assert calls == []
