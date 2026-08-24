"""resume_setup.py wipes stale glossary fragments before a run so a later wait
step cannot poll a prior run's out_{i}_attempt_{n}.json (or a stale approved_*
snapshot) while assuming it is absent (LT 1.16.0, Facet A of "bind the merge to
the bytes that were reviewed"). The rule is conditioned on `resume`:

  fresh (resume False): wipe ALL out_* and approved_* attempts, incl. attempt 0
  resume (resume True): keep out_{i}_attempt_0, wipe out_* n>=1 and ALL approved_*

Nothing else deletes fragments and a MATCH-resume reuses the same run_id, so on
a fresh-id collision with an orphaned glossary dir a surviving attempt 0 would
be audited stale -- which is why a fresh run keeps nothing.

LT 1.16.1 (#347) adds a THIRD kind of stale artifact, with its own rule:

  evidence_{i}_attempt_{n}/ -- wiped UNCONDITIONALLY, fresh and resume alike,
  attempt 0 included.

The asymmetry with out_* is the point, and this file locks it as such. Evidence
directories follow the approved_* rule: they are an OUTPUT of the citation
review, re-produced by the prepare step that runs before anything judges them,
so a surviving copy is never useful and is potentially wrong. Keeping attempt
0's FRAGMENT is what the resume-skip optimisation depends on; keeping attempt
0's EVIDENCE buys nothing and would leave a prior run's fetched page bodies at
exactly the paths this run writes.

Two traps this file is shaped around. First, the pre-#347 wipe could not have
removed these even if its regex had matched: they are DIRECTORIES and the
fragment path calls entry.unlink(). Second, and less obvious: an EMPTY
directory is removed by a bare rmdir() too, so a test seeding one would stay
green against an implementation that silently fails on real content -- which is
the only content an evidence dir ever has. Every evidence dir seeded here is
non-empty and nested.
"""

import importlib.util
import re
from pathlib import Path

import pytest

_RS_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills/literary-translator/assets/scripts/resume_setup.py"
)


@pytest.fixture(scope="module")
def rs():
    spec = importlib.util.spec_from_file_location("resume_setup_under_test", _RS_PATH)
    assert spec is not None and spec.loader is not None, f"could not load spec for {_RS_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FRAGMENTS = (
    "out_0_attempt_0.json",
    "out_0_attempt_1.json",
    "out_1_attempt_0.json",
    "out_2_attempt_3.json",
    "approved_0_attempt_0.json",
    "approved_1_attempt_2.json",
    # #723 verdict records. They ride the approved_* rule, not the out_* one --
    # see the wipe's own docstring for why a record of unknown age is worse than
    # no record at all.
    "approval_0_attempt_0.json",
    "approval_1_attempt_2.json",
)

# The only two fragments a RESUME keeps.
_RESUME_SURVIVING_FRAGMENTS = ("out_0_attempt_0.json", "out_1_attempt_0.json")

# #347 evidence directories, named exactly as the code that writes them names
# them: glossary-pass-wf.template.js's evidenceDir() builds
# RUN_DIR + "/evidence_" + index + "_attempt_" + attempt, and fetch_citation.py
# fills it with one body file per admitted url plus index.json. Seeded from the
# writer's own convention rather than from prose, so a rename there surfaces
# here as a red test instead of a test that quietly stops describing anything.
_EVIDENCE_DIRS = (
    "evidence_0_attempt_0",
    "evidence_0_attempt_1",
    "evidence_2_attempt_3",
)

# Entries the wipe must never touch, under EITHER flag. The evidence_* ones are
# NEAR MISSES on purpose -- a file whose name merely ends in the attempt-dir
# form, a non-numeric index, a bare prefix. Without them a regex loosened to
# `evidence_.*` would be indistinguishable from the real anchored one, and the
# blast radius of that mistake is somebody else's directory.
_UNTOUCHED_FILES = (
    "manifest_0.json",
    "manifest_all.json",
    "input.digest",
    "evidence_0_attempt_0.json",
    "evidence_all.json",
    # Near misses on the #723 record name, for the same reason the evidence_*
    # near misses exist above: a pattern loosened to `approval_.*` would be
    # indistinguishable from the anchored one.
    "approval_all.json",
    "approval_x_attempt_0.json",
)
_UNTOUCHED_DIRS = (
    "evidence_x_attempt_0",
    "evidence_summary",
)
_UNTOUCHED = tuple(sorted(_UNTOUCHED_FILES + _UNTOUCHED_DIRS))


def _seed_evidence_dir(path: Path) -> None:
    """One evidence directory with realistic, NON-EMPTY, nested content.

    Non-empty is load-bearing: rmdir() removes an empty directory just as
    rmtree() does, so an empty fixture would pass against an implementation
    that fails on every directory this ever actually has."""
    (path / "nested").mkdir(parents=True, exist_ok=True)
    (path / "index.json").write_text('{"entries": []}', encoding="utf-8")
    (path / "body_0.html").write_text("<html>stale fetched page</html>", encoding="utf-8")
    (path / "nested" / "deeper.txt").write_text("x", encoding="utf-8")


def _seed(dirpath: Path) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    for name in _FRAGMENTS + _UNTOUCHED_FILES:
        (dirpath / name).write_text("x", encoding="utf-8")
    for name in _UNTOUCHED_DIRS:
        (dirpath / name).mkdir()
        (dirpath / name / "keep.txt").write_text("x", encoding="utf-8")
    for name in _EVIDENCE_DIRS:
        _seed_evidence_dir(dirpath / name)


def _names(dirpath: Path):
    return sorted(p.name for p in dirpath.iterdir())


def test_fresh_run_wipes_every_attempt_including_zero(rs, tmp_path):
    d = tmp_path / "glossary" / "runs" / "R"
    _seed(d)
    rs._wipe_stale_glossary_fragments(d, resume=False)
    assert _names(d) == list(_UNTOUCHED), (
        "a fresh run must trust nothing on disk -- every out_*, approved_*, "
        "approval_* and evidence_* attempt, including attempt 0, must go. "
        f"Survivors: {_names(d)}"
    )


def test_resume_keeps_attempt_zero_but_wipes_the_rest(rs, tmp_path):
    d = tmp_path / "glossary" / "runs" / "R"
    _seed(d)
    rs._wipe_stale_glossary_fragments(d, resume=True)
    assert _names(d) == sorted(_UNTOUCHED + _RESUME_SURVIVING_FRAGMENTS), (
        "a resume keeps out_{i}_attempt_0 (the resume-skip optimisation depends "
        "on it and it is still citation-reviewed) but must wipe every attempt "
        ">=1, every approved_* snapshot, every approval_* verdict record and "
        f"every evidence_* directory. Survivors: {_names(d)}"
    )


@pytest.mark.parametrize("resume", [False, True])
def test_every_verdict_record_is_wiped_under_either_flag(rs, tmp_path, resume):
    """#723. The record answers "batch i, these exact bytes, passed the citation
    review", and an operator reads it to pick the attested snapshot by digest.
    That is only true while it describes THIS run: a record inherited from an
    earlier run would vouch for bytes this run may already have rejected and
    re-generated, which is precisely the guesswork the record removes. So it
    follows approved_*, wiped under either flag, rather than out_*.

    Stated on its own rather than left to the exact-list assertions above: those
    two would also stay green if the record were merely never SEEDED, and a
    failure there prints a directory diff rather than the property."""
    d = tmp_path / "glossary" / "runs" / "R"
    _seed(d)
    rs._wipe_stale_glossary_fragments(d, resume=resume)
    survivors = [
        n for n in _names(d) if re.fullmatch(r"approval_\d+_attempt_\d+\.json", n)
    ]
    assert survivors == [], (
        "no verdict record may survive a wipe under either flag -- a record of "
        f"unknown age is worse than none. Survivors: {survivors}"
    )
    # ...and the near miss is still there, so the pattern is anchored.
    assert "approval_x_attempt_0.json" in _names(d)
    assert "approval_all.json" in _names(d)


# ---------------------------------------------------------------------------
# #347 -- evidence directories. The exact-list assertions above already carry
# these transitively; the tests below state each half on its own so a failure
# names the property that broke rather than printing a directory diff.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("resume", [False, True])
def test_every_evidence_directory_is_wiped_under_either_flag(rs, tmp_path, resume):
    """The unconditional rule, both flags exercised from one body so neither can
    drift green while the other rots. Parametrised rather than duplicated
    because the assertion is identical -- that is the whole claim."""
    d = tmp_path / "glossary" / "runs" / "R"
    _seed(d)
    rs._wipe_stale_glossary_fragments(d, resume=resume)
    survivors = [n for n in _names(d) if n in _EVIDENCE_DIRS]
    assert survivors == [], (
        f"evidence directories survived a resume={resume} wipe: {survivors}. They are "
        f"an OUTPUT of the citation review, re-produced by the prepare step before "
        f"anything judges them, so a survivor is never useful and sits at exactly "
        f"the path this run writes"
    )


def test_resume_keeps_fragment_attempt_zero_but_never_its_evidence(rs, tmp_path):
    """THE asymmetry, asserted as one pair so it cannot be read as an oversight.

    Same batch, same attempt 0, opposite fates: out_0_attempt_0.json stays
    because the resume-skip optimisation depends wholly on it, and
    evidence_0_attempt_0/ goes because evidence follows the approved_* rule.
    The plausible-looking wrong implementation -- treating evidence like out_*
    and sparing attempt 0 on a resume -- passes every other test in this file."""
    d = tmp_path / "glossary" / "runs" / "R"
    _seed(d)
    rs._wipe_stale_glossary_fragments(d, resume=True)

    assert (d / "out_0_attempt_0.json").is_file(), (
        "a resume must KEEP the attempt-0 fragment -- the resume-skip "
        "optimisation depends wholly on it"
    )
    assert not (d / "evidence_0_attempt_0").exists(), (
        "a resume must WIPE attempt 0's evidence directory even though it keeps "
        "attempt 0's fragment. Evidence follows the approved_* rule, not the out_* "
        "one: sparing it buys nothing and leaves a previous run's fetched page "
        "bodies at exactly the paths this run writes"
    )


@pytest.mark.parametrize("resume", [False, True])
def test_evidence_wipe_removes_a_non_empty_nested_directory(rs, tmp_path, resume):
    """Property 4, isolated. An empty directory falls to rmdir() as readily as to
    rmtree(), so this is the only test here that can tell the two apart -- and a
    real evidence directory is never empty."""
    d = tmp_path / "glossary" / "runs" / "R"
    d.mkdir(parents=True)
    target = d / "evidence_7_attempt_2"
    _seed_evidence_dir(target)
    assert (target / "nested" / "deeper.txt").is_file(), "fixture did not seed real content"

    rs._wipe_stale_glossary_fragments(d, resume=resume)

    assert not target.exists(), (
        "a NON-EMPTY evidence directory survived the wipe. rmdir() refuses a "
        "populated directory and unlink() refuses a directory outright -- either "
        "would leave every real evidence dir on disk while an empty-dir test "
        "stayed green"
    )
    assert _names(d) == [], f"unexpected leftovers: {_names(d)}"


@pytest.mark.parametrize("resume", [False, True])
def test_a_plain_file_named_like_an_evidence_dir_does_not_break_the_wipe(rs, tmp_path, resume):
    """The is_dir() guard, and why it is not decoration.

    shutil.rmtree() raises NotADirectoryError on a plain file. Without the
    guard, one stray file matching the evidence name form would abort the wipe
    MID-ITERATION -- leaving the run half-wiped, which is worse than not wiping
    at all, because the surviving half looks deliberately kept."""
    d = tmp_path / "glossary" / "runs" / "R"
    _seed(d)
    (d / "evidence_9_attempt_9").write_text("a file, not a directory", encoding="utf-8")

    rs._wipe_stale_glossary_fragments(d, resume=resume)  # must not raise

    expected = sorted(
        _UNTOUCHED
        + ("evidence_9_attempt_9",)
        + (_RESUME_SURVIVING_FRAGMENTS if resume else ())
    )
    assert _names(d) == expected, (
        f"a plain file named like an evidence directory must be left alone (it "
        f"matches no fragment pattern) and must not stop the rest of the wipe. "
        f"Survivors: {_names(d)}"
    )


def test_wipe_is_a_noop_on_an_empty_dir(rs, tmp_path):
    d = tmp_path / "glossary" / "runs" / "R"
    d.mkdir(parents=True)
    rs._wipe_stale_glossary_fragments(d, resume=False)  # must not raise
    assert _names(d) == []


@pytest.mark.parametrize("resume", [True, False])
def test_write_run_dir_glossary_passes_the_real_resume_flag(rs, tmp_path, monkeypatch, resume):
    """Wiring: write_run_dir's glossary branch must invoke the wipe with the
    run's actual resume flag and the run-scoped glossary dir -- a spy proves the
    call without depending on manifest writing. Both flag values are exercised:
    a hardcoded `True` at the call site would keep every other test green while
    silently letting a FRESH run adopt an orphaned dir's attempt 0."""
    monkeypatch.setattr(rs, "DURABLE_ROOT", tmp_path)
    monkeypatch.setattr(rs, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(rs, "write_glossary_manifests", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(
        rs, "_wipe_stale_glossary_fragments", lambda d, resume: calls.append((d, resume))
    )
    (tmp_path / "runs").mkdir()

    run_id = "20260101T000000Z"
    rs.write_run_dir(
        run_id, resume=resume, input_digest="d", kind="glossary", payload={"batches": {}}
    )
    assert calls == [(tmp_path / "glossary" / "runs" / run_id, resume)], (
        f"the glossary branch must forward the run's own resume flag ({resume}), not a "
        f"constant -- otherwise the fresh-vs-resume wipe rule is unreachable. Got: {calls}"
    )


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


def test_a_trailing_newline_name_is_not_wiped(rs):
    """The anchor matters because this regex gates a shutil.rmtree.

    Python's `$` also matches BEFORE a trailing newline, so `^...$` admitted
    "evidence_0_attempt_1\\n" -- a directory name POSIX allows -- and a name that
    matches by accident is deleted by accident. Same anchor defect this release
    fixed once in the content-type gate; the sibling in the DESTRUCTIVE path had
    kept the loose form.
    """
    assert rs._GLOSSARY_EVIDENCE_DIR_RE.match("evidence_0_attempt_1")
    assert not rs._GLOSSARY_EVIDENCE_DIR_RE.match("evidence_0_attempt_1\n")
    assert not rs._GLOSSARY_EVIDENCE_DIR_RE.match("xevidence_0_attempt_1")
