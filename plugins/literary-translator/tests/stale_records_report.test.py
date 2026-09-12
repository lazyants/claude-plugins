"""tests/stale_records_report.test.py -- `stale_records_report.py --prep` /
`--build`, the report-only stale-record pass an operator runs after
correcting a class of renderings across already-converged drafts (#931).

WHAT IS BEING GUARDED. The population is converged ledger fragments only
(`runs/ledger.d/<seg>.json`, status == "converged"); the fragment's own
`reviewed_draft_sha1` is deliberately NOT compared -- the operator has just
hand-edited these drafts, and a mismatch there is the expected state, not an
exclusion. `--prep` computes the deterministic `names[]` predicate and
freezes each segment's current `draft_sha1` (via the sibling `draft_sha1.py`,
never recomputed by hand); `--build` reads a per-segment judge verdict for
`notes[]`, gates it against the draft the verdict claims to have judged, and
merges everything into one human-readable report. Every gate is checked
before anything is written, so a single bad verdict must leave a
pre-existing report byte-identical rather than half-overwritten.

Follows this plugin's dominant test pattern (see plugin-facts.md's "Test
conventions" and tests/person_registry_prep.test.py): the SHIPPED script is
copied into an isolated `tmp_path/durable_root/scripts/` and driven as a
subprocess with `sys.executable`, never a hand-shaped stand-in for what the
pipeline emits. The one exception is the names[] predicate itself, which is
loaded in-process (see `_load_module()` below) so its boundary cases --
NFC normalisation, a bare initial, an all-lowercase gloss -- can be pinned
directly against the pure function rather than reconstructed through a whole
prep run each time.

The `--build` happy path is the INTEGRATION case on purpose: it feeds the
REAL `--prep` output's `draft_sha1` into hand-written verdict files, never a
hand-built `prep.json`, because the join between the two files is exactly
what a hand-built prep.json would paper over.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPT = ASSETS / "scripts" / "stale_records_report.py"
JSON_STDOUT = ASSETS / "scripts" / "json_stdout.py"
DRAFT_SHA1 = ASSETS / "scripts" / "draft_sha1.py"
TEMPLATE = ASSETS / "templates" / "stale_notes_TASK.template.md"

assert SCRIPT.is_file(), f"stale_records_report.py not found at {SCRIPT}"

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _agent_definition import AGENTS_DIR, read_frontmatter, tool_allowlist  # noqa: E402

AGENT_DEF = AGENTS_DIR / "stale-notes-judge.md"

# json.loads() on ~10**6 levels of array nesting raises RecursionError
# (CPython's C stack-depth guard: "Stack overflow ... while decoding a JSON
# array") -- a RuntimeError subclass, NOT a ValueError subclass, so it is a
# distinct failure mode from a JSONDecodeError/UnicodeDecodeError and slips
# past an `except (OSError, ValueError)` read exactly like the earlier
# surrogate/UTF-8 crashes. Shared across the fragment/draft/verdict variants
# below since it is the same adversarial input at three different read sites.
_DEEP_NESTING = "[" * 10**6 + "]" * 10**6


def _load_module():
    """Load the shipped script in-process for the pure names[] predicate --
    the CLI-driving tests below still go through the subprocess."""
    spec = importlib.util.spec_from_file_location("stale_records_report_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def _write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# The fixture root. One converged segment with a real substance -- three
# names[] rows spanning flagged/clean/unverifiable, three notes[] entries
# spanning stale/provenance/current -- one converged FRONTBACK:{id} unit
# (both live books carry colon-bearing ids: it must survive fragment stem ->
# draft path -> verdict filename -> report unmangled), and one draft whose
# fragment is present but NOT converged (drafts_not_selected).
# ---------------------------------------------------------------------------

SEG01_BLOCK_TEXT = "He came from Kremenchug and later settled in Uman with his family."
SEG01_BLOCKS = {"PARA:seg01:0001": SEG01_BLOCK_TEXT}

SEG01_NAMES = [
    {"source_form": "town_x", "canonical_target_form": "in Krimintshuk",
     "basis": "transliterated", "confidence": "medium"},
    {"source_form": "town_y", "canonical_target_form": "Uman",
     "basis": "transliterated", "confidence": "high"},
    {"source_form": "phrase_z", "canonical_target_form": "without end",
     "basis": "not_a_name", "confidence": "low"},
]

# notes[0] carries a REAL embedded newline on purpose -- it is the fixture
# for both the multiline-note rendering case (STALE_RECORDS.md's "> note"
# line must fold it to one line) and, corrupted per-test, the
# verdict_string_multiline gate (a forged quoted_form spanning the same
# boundary).
SEG01_NOTES = [
    "The town was earlier\nrendered as \"Krimintshak\" in the draft.",
    "Uman was previously spelled differently in early drafts.",
    "The narrator arrives in Uman by cart.",
]

SEG01_DRAFT = {
    "seg": "seg01",
    "blocks": SEG01_BLOCKS,
    "footnotes": {},
    "verses": {},
    "names": SEG01_NAMES,
    "notes": SEG01_NOTES,
    "dispatch_token": "tok-seg01",
}

FM01_BLOCK_TEXT = "A short preface about Uman and its people."
FM01_NOTES = ["This preface was regenerated for the target language."]
# One UNVERIFIABLE names row (an all-lowercase gloss, no qualifying token) and
# a "current" note: this segment is the round-2 regression case for
# render_markdown's "(clean)" gate -- zero FLAGGED rows and zero
# stale/provenance notes used to be conflated with "nothing to look at", but
# an unverifiable count is something a human still has to see.
FM01_NAMES = [
    {"source_form": "phrase_fm", "canonical_target_form": "of blessed memory",
     "basis": "not_a_name", "confidence": "low"},
]
FM01_DRAFT = {
    "seg": "FRONTBACK:fm01",
    "blocks": {"PARA:FRONTBACK:fm01:0001": FM01_BLOCK_TEXT},
    "footnotes": {},
    "verses": {},
    "names": FM01_NAMES,
    "notes": FM01_NOTES,
    "dispatch_token": "tok-fm01",
}

SEG03_DRAFT = {
    "seg": "seg03",
    "blocks": {"PARA:seg03:0001": "An unrelated draft still awaiting review."},
    "footnotes": {},
    "verses": {},
    "names": [],
    "notes": [],
    "dispatch_token": "tok-seg03",
}


def _copy_scripts(scripts_dir: Path) -> None:
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for src in (SCRIPT, JSON_STDOUT, DRAFT_SHA1):
        shutil.copy2(src, scripts_dir / src.name)


def build_root(tmp_path: Path) -> Path:
    """The main fixture: two converged segments (seg01, FRONTBACK:fm01) and
    one non-converged one (seg03, present on disk but not selected)."""
    root = tmp_path / "durable_root"
    _copy_scripts(root / "scripts")
    (root / "segments").mkdir(parents=True, exist_ok=True)
    (root / "runs" / "ledger.d").mkdir(parents=True, exist_ok=True)

    _write_json(root / "segments" / "seg01.draft.json", SEG01_DRAFT)
    _write_json(root / "segments" / "FRONTBACK:fm01.draft.json", FM01_DRAFT)
    _write_json(root / "segments" / "seg03.draft.json", SEG03_DRAFT)

    # reviewed_draft_sha1 is deliberately NOT compared by this pass (see the
    # module docstring), so a placeholder value is as good as a real one.
    _write_json(root / "runs" / "ledger.d" / "seg01.json",
                {"seg": "seg01", "status": "converged", "reviewed_draft_sha1": "0" * 40})
    _write_json(root / "runs" / "ledger.d" / "FRONTBACK:fm01.json",
                {"seg": "FRONTBACK:fm01", "status": "converged", "reviewed_draft_sha1": "0" * 40})
    _write_json(root / "runs" / "ledger.d" / "seg03.json",
                {"seg": "seg03", "status": "in_review"})
    return root


def _minimal_root(tmp_path: Path) -> Path:
    """A bare durable root (scripts only) for the zero-converged-population
    exit-2 cases, which must not see seg01/FRONTBACK:fm01 at all."""
    root = tmp_path / "durable_root_min"
    _copy_scripts(root / "scripts")
    (root / "segments").mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def root(tmp_path):
    return build_root(tmp_path)


# ---------------------------------------------------------------------------
# Subprocess plumbing. `parse_summary` is the shared stdout/exit-code
# contract every test below goes through: exactly one JSON line for exit
# 0/1, EMPTY stdout for exit 2 -- a usage/precondition/corruption refusal
# reports on stderr only.
# ---------------------------------------------------------------------------

def _run(root: Path, *args: str, timeout: float = 30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "stale_records_report.py"), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _prep(root: Path):
    return _run(root, "--prep")


def _build(root: Path):
    return _run(root, "--build")


def parse_summary(proc):
    if proc.returncode == 2:
        assert proc.stdout == "", proc.stdout
        return None
    lines = proc.stdout.splitlines()
    assert len(lines) == 1, (proc.returncode, proc.stdout, proc.stderr)
    return json.loads(lines[0])


def _assert_fatal(proc, reason: str):
    """The exit-2 contract: empty stdout, the reason named on stderr."""
    payload = parse_summary(proc)
    assert proc.returncode == 2, (payload, proc.stderr)
    assert payload is None
    assert reason in proc.stderr, proc.stderr
    return proc


# ---------------------------------------------------------------------------
# --prep: the population and the names[] predicate, driven end to end.
# ---------------------------------------------------------------------------

def test_prep_selects_converged_segments_and_counts_names(root):
    proc = _prep(root)
    payload = parse_summary(proc)
    assert proc.returncode == 0, (payload, proc.stderr)
    assert payload["success"] is True
    assert payload["mode"] == "prep"
    assert payload["segments_selected"] == 2
    assert payload["drafts_not_selected"] == 1
    assert payload["names_total"] == 4  # seg01's 3 + FRONTBACK:fm01's 1
    assert payload["names_flagged"] == 1
    assert payload["names_unverifiable"] == 2  # seg01's "without end" + fm01's "of blessed memory"
    assert payload["names_unreadable"] == 0
    assert payload["notes_total"] == 4

    prep_path = root / "stale_records" / "prep.json"
    names_report_path = root / "stale_records" / "names_report.json"
    assert prep_path.is_file()
    assert names_report_path.is_file()
    assert payload["prep_path"] == str(prep_path)
    assert payload["names_report_path"] == str(names_report_path)


def test_prep_writes_the_sha_the_sibling_authority_computes(root):
    """The sha in prep.json must be draft_sha1.draft_content_sha1(path),
    CALLED from the copied sibling -- never recomputed by this test or by
    the script's own hand."""
    proc = _prep(root)
    payload = parse_summary(proc)
    assert proc.returncode == 0, (payload, proc.stderr)

    doc = json.loads((root / "stale_records" / "prep.json").read_text(encoding="utf-8"))
    segs = {s["seg"]: s for s in doc["segments"]}
    assert set(segs) == {"seg01", "FRONTBACK:fm01"}

    sys.path.insert(0, str(root / "scripts"))
    try:
        import draft_sha1 as ds
        for seg, row in segs.items():
            expected = ds.draft_content_sha1(root / "segments" / f"{seg}.draft.json")
            assert row["draft_sha1"] == expected
    finally:
        sys.path.remove(str(root / "scripts"))
        sys.modules.pop("draft_sha1", None)


def test_prep_names_report_carries_the_flagged_row_and_its_missing_tokens(root):
    proc = _prep(root)
    payload = parse_summary(proc)
    assert proc.returncode == 0, (payload, proc.stderr)

    doc = json.loads((root / "stale_records" / "names_report.json").read_text(encoding="utf-8"))
    by_seg = {s["seg"]: s for s in doc["segments"]}
    seg01 = by_seg["seg01"]
    assert seg01["names_total"] == 3
    assert seg01["names_flagged"] == 1
    assert seg01["names_unverifiable"] == 1

    rows_by_index = {r["index"]: r for r in seg01["rows"]}
    flagged = rows_by_index[0]
    assert flagged["source_form"] == "town_x"
    assert flagged["canonical_target_form"] == "in Krimintshuk"
    assert flagged["missing_tokens"] == ["Krimintshuk"]

    fm01 = by_seg["FRONTBACK:fm01"]
    assert fm01["names_total"] == 1
    assert fm01["names_flagged"] == 0
    assert fm01["names_unverifiable"] == 1
    assert fm01["rows"] == []  # unverifiable rows are never itemised, only counted


def test_prep_ignores_a_non_converged_draft_but_counts_it(root):
    proc = _prep(root)
    payload = parse_summary(proc)
    assert proc.returncode == 0, (payload, proc.stderr)
    doc = json.loads((root / "stale_records" / "prep.json").read_text(encoding="utf-8"))
    assert "seg03" not in {s["seg"] for s in doc["segments"]}
    assert payload["drafts_not_selected"] == 1


# ---------------------------------------------------------------------------
# --prep: zero converged segments and corruption, both exit 2, empty stdout.
# ---------------------------------------------------------------------------

def test_prep_refuses_when_ledger_d_is_absent(tmp_path):
    # An absent ledger.d is "nothing has converged yet", not an error --
    # read_ledger_fragments() returns {} and cmd_prep names the SAME reason
    # as any other empty population: no_converged_segments.
    root = _minimal_root(tmp_path)
    _assert_fatal(_prep(root), "no_converged_segments")


def test_prep_refuses_when_ledger_d_is_a_plain_file(tmp_path):
    # NotADirectoryError hits the exact same benign branch as ENOENT.
    root = _minimal_root(tmp_path)
    (root / "runs" / "ledger.d").write_text("not a directory", encoding="utf-8")
    _assert_fatal(_prep(root), "no_converged_segments")


def test_prep_refuses_when_no_fragment_is_converged(tmp_path):
    root = _minimal_root(tmp_path)
    (root / "runs" / "ledger.d").mkdir(parents=True, exist_ok=True)
    _write_json(root / "runs" / "ledger.d" / "seg03.json",
                {"seg": "seg03", "status": "in_review"})
    _assert_fatal(_prep(root), "no_converged_segments")


def test_prep_refuses_when_ledger_d_is_unreadable(tmp_path):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses permission bits")
    root = _minimal_root(tmp_path)
    ledger_d = root / "runs" / "ledger.d"
    ledger_d.mkdir(parents=True, exist_ok=True)
    _write_json(ledger_d / "seg01.json",
                {"seg": "seg01", "status": "converged", "reviewed_draft_sha1": "a" * 40})
    ledger_d.chmod(0o000)
    try:
        try:
            list(ledger_d.iterdir())
        except PermissionError:
            pass
        else:
            pytest.skip("this environment does not enforce directory permission bits")
        _assert_fatal(_prep(root), "ledger_d_unreadable")
    finally:
        ledger_d.chmod(0o755)


def test_prep_refuses_when_a_fragment_is_not_valid_json(tmp_path):
    root = _minimal_root(tmp_path)
    ledger_d = root / "runs" / "ledger.d"
    ledger_d.mkdir(parents=True, exist_ok=True)
    (ledger_d / "seg01.json").write_text("not { valid json", encoding="utf-8")
    _assert_fatal(_prep(root), "fragment_unreadable")


def test_prep_refuses_when_a_fragment_contains_pathologically_deep_json_nesting(tmp_path):
    root = _minimal_root(tmp_path)
    ledger_d = root / "runs" / "ledger.d"
    ledger_d.mkdir(parents=True, exist_ok=True)
    (ledger_d / "seg01.json").write_text(_DEEP_NESTING, encoding="utf-8")
    _assert_fatal(_prep(root), "fragment_unreadable")


def test_prep_refuses_when_a_fragment_is_not_a_json_object(tmp_path):
    root = _minimal_root(tmp_path)
    ledger_d = root / "runs" / "ledger.d"
    ledger_d.mkdir(parents=True, exist_ok=True)
    (ledger_d / "seg01.json").write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    _assert_fatal(_prep(root), "fragment_not_object")


def test_prep_refuses_when_a_fragment_has_no_string_status(tmp_path):
    root = _minimal_root(tmp_path)
    ledger_d = root / "runs" / "ledger.d"
    ledger_d.mkdir(parents=True, exist_ok=True)
    _write_json(ledger_d / "seg01.json", {"seg": "seg01"})  # no "status" key at all
    _assert_fatal(_prep(root), "fragment_status_invalid")


def test_prep_refuses_when_a_converged_segment_has_no_draft(root):
    (root / "segments" / "seg01.draft.json").unlink()
    proc = _assert_fatal(_prep(root), "draft_missing_for_converged")
    assert "seg01" in proc.stderr


def test_prep_refuses_when_a_converged_segments_draft_is_not_valid_json(root):
    (root / "segments" / "seg01.draft.json").write_text("not { valid json", encoding="utf-8")
    _assert_fatal(_prep(root), "draft_unreadable")


def test_prep_refuses_when_a_converged_segments_draft_contains_pathologically_deep_json_nesting(root):
    (root / "segments" / "seg01.draft.json").write_text(_DEEP_NESTING, encoding="utf-8")
    _assert_fatal(_prep(root), "draft_unreadable")


def _corrupt_seg01_draft_field(root: Path, field: str, value) -> None:
    draft_path = root / "segments" / "seg01.draft.json"
    doc = json.loads(draft_path.read_text(encoding="utf-8"))
    doc[field] = value
    draft_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_prep_refuses_draft_shape_when_notes_is_a_string(root):
    _corrupt_seg01_draft_field(root, "notes", "not a list")
    proc = _assert_fatal(_prep(root), "draft_shape")
    assert "seg01" in proc.stderr
    assert "notes" in proc.stderr


def test_prep_refuses_draft_shape_when_names_is_an_object(root):
    _corrupt_seg01_draft_field(root, "names", {"not": "a list"})
    proc = _assert_fatal(_prep(root), "draft_shape")
    assert "seg01" in proc.stderr
    assert "names" in proc.stderr


def test_prep_refuses_draft_shape_when_a_notes_entry_is_an_int(root):
    _corrupt_seg01_draft_field(root, "notes", [SEG01_NOTES[0], 42, SEG01_NOTES[2]])
    proc = _assert_fatal(_prep(root), "draft_shape")
    assert "seg01" in proc.stderr


def test_prep_refuses_draft_shape_when_blocks_is_a_list(root):
    _corrupt_seg01_draft_field(root, "blocks", ["not", "a", "dict"])
    proc = _assert_fatal(_prep(root), "draft_shape")
    assert "seg01" in proc.stderr
    assert "blocks" in proc.stderr


def test_prep_refuses_draft_shape_when_a_block_value_is_not_a_string(root):
    """A non-string block VALUE (the block id maps to an object/list/number
    instead of prose) used to be silently dropped from the NFC-joined
    corpus (`blocks_corpus_nfc`'s `isinstance(v, str)` filter) -- corruption
    read as "one less block to check against", never a refusal."""
    draft_path = root / "segments" / "seg01.draft.json"
    doc = json.loads(draft_path.read_text(encoding="utf-8"))
    block_id = next(iter(doc["blocks"]))
    doc["blocks"][block_id] = {"not": "a string"}
    draft_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    proc = _assert_fatal(_prep(root), "draft_shape")
    assert "seg01" in proc.stderr
    assert block_id in proc.stderr


def test_prep_refuses_when_the_draft_sha1_sibling_cannot_be_imported(tmp_path):
    """sibling_import_failed: a durable root that never got draft_sha1.py
    copied into scripts/ alongside the shipped script -- the same self-
    anchored sys.path.insert(SCRIPTS_DIR) import person_registry.py's own
    import_siblings() uses, and the same failure mode."""
    root = tmp_path / "durable_root_no_sibling"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
    shutil.copy2(JSON_STDOUT, root / "scripts" / JSON_STDOUT.name)
    # draft_sha1.py deliberately NOT copied.
    (root / "segments").mkdir(parents=True, exist_ok=True)
    (root / "runs" / "ledger.d").mkdir(parents=True, exist_ok=True)
    _write_json(root / "segments" / "seg01.draft.json", SEG01_DRAFT)
    _write_json(root / "runs" / "ledger.d" / "seg01.json",
                {"seg": "seg01", "status": "converged", "reviewed_draft_sha1": "0" * 40})

    _assert_fatal(_prep(root), "sibling_import_failed")


def test_build_succeeds_without_prep_json_or_names_report_json_on_disk(root):
    """Round 2: --build no longer READS prep.json/names_report.json -- it
    re-derives the population, draft hashes, and names rows straight from
    the ledger/segments on disk, the same way --prep does. prep.json is
    still how an operator (and this test) LEARNS the current draft_sha1 to
    put in a verdict; once the verdicts are written, --build itself never
    has to open either file, and deleting them proves it."""
    _write_verdicts(root, _prepped_verdicts(root))
    (root / "stale_records" / "prep.json").unlink()
    (root / "stale_records" / "names_report.json").unlink()

    proc = _build(root)
    payload = parse_summary(proc)
    assert proc.returncode == 0, (payload, proc.stderr)
    assert payload["success"] is True
    assert payload["segments_selected"] == 2
    assert payload["names_total"] == 4
    assert payload["names_flagged"] == 1
    assert payload["names_unverifiable"] == 2
    assert payload["notes_total"] == 4
    assert payload["notes_stale"] == 1
    assert payload["notes_provenance"] == 1
    assert (root / "stale_records" / "stale_records_report.json").is_file()
    assert (root / "stale_records" / "STALE_RECORDS.md").is_file()


def test_build_picks_up_a_segment_that_converged_after_prep_ran(root):
    """--build recomputes the converged population FRESH on every call
    (select_converged_fragments()) -- a fragment that flips to "converged"
    after --prep already ran is part of --build's own scan and, having no
    verdict file yet, is correctly refused as verdict_missing rather than
    silently ignored because --prep never saw it."""
    _write_verdicts(root, _prepped_verdicts(root))
    # seg03 was NOT converged when --prep ran; flip it now.
    _write_json(root / "runs" / "ledger.d" / "seg03.json",
                {"seg": "seg03", "status": "converged", "reviewed_draft_sha1": "0" * 40})
    payload = _assert_refusal(root, "verdict_missing")
    assert "seg03" in payload.get("missing_segments", [])


def test_prep_refuses_when_prep_json_target_is_a_directory(root):
    """The up-front `report_target_not_a_file` check runs BEFORE anything is
    staged: an EMPTY directory at prep.json's own path is not a regular
    file, checked and refused before any temp file is even written --
    distinct from report_write_failed, which is an OSError raised DURING
    staging/replacing."""
    blocker = root / "stale_records" / "prep.json"
    blocker.mkdir(parents=True, exist_ok=True)  # empty -- no os.replace would even fail here
    proc = _assert_fatal(_prep(root), "report_target_not_a_file")
    assert str(blocker) in proc.stderr or "prep.json" in proc.stderr
    assert blocker.is_dir()  # untouched
    leftover_tmp = [p.name for p in (root / "stale_records").glob(".*") if p.is_file()]
    assert leftover_tmp == [], leftover_tmp


def test_build_refuses_when_stale_records_md_target_is_a_symlink(root, tmp_path):
    """Same up-front check, the other non-regular-file shape: a symlink at
    the target path. The linked-to file, elsewhere entirely, must be left
    untouched -- this check must never follow the link and write through
    it."""
    _write_verdicts(root, _prepped_verdicts(root))
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("untouched content", encoding="utf-8")
    link = root / "stale_records" / "STALE_RECORDS.md"
    link.symlink_to(elsewhere)
    proc = _assert_fatal(_build(root), "report_target_not_a_file")
    assert elsewhere.read_text(encoding="utf-8") == "untouched content"
    leftover_tmp = [p.name for p in (root / "stale_records").glob(".*") if p.is_file()]
    assert leftover_tmp == [], leftover_tmp


def test_prep_refuses_when_names_report_json_target_is_a_non_empty_directory(root):
    """The up-front report_target_not_a_file check fires BEFORE staging --
    a non-empty directory at the target is refused there, never reaching
    the os.replace() that would have raised report_write_failed."""
    blocker = root / "stale_records" / "names_report.json"
    blocker.mkdir(parents=True, exist_ok=True)
    (blocker / "occupied").write_text("x", encoding="utf-8")
    _assert_fatal(_prep(root), "report_target_not_a_file")


def test_build_refuses_when_stale_records_md_target_is_a_non_empty_directory(root):
    _write_verdicts(root, _prepped_verdicts(root))
    blocker = root / "stale_records" / "STALE_RECORDS.md"
    blocker.mkdir(parents=True, exist_ok=True)
    (blocker / "occupied").write_text("x", encoding="utf-8")
    _assert_fatal(_build(root), "report_target_not_a_file")


def test_prep_refuses_report_write_failed_when_stale_records_dir_is_unwritable(tmp_path):
    """A genuine OSError DURING staging, distinct from the up-front target-
    type check above: prep.json/names_report.json don't exist yet in a
    fresh root, so that check has nothing to refuse -- it is stale_records/
    itself lacking write permission that makes even creating the first
    `.prep.json.tmp.<pid>` fail."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses permission bits")
    root = build_root(tmp_path)
    stale_records = root / "stale_records"
    stale_records.mkdir(parents=True, exist_ok=True)
    stale_records.chmod(0o500)
    try:
        probe = stale_records / "probe.tmp"
        try:
            probe.write_text("x", encoding="utf-8")
        except PermissionError:
            pass
        else:
            probe.unlink()
            pytest.skip("this environment does not enforce directory write permission bits")
        _assert_fatal(_prep(root), "report_write_failed")
    finally:
        stale_records.chmod(0o755)


def test_build_refuses_report_write_failed_when_a_verdict_reason_is_a_lone_surrogate(root):
    """A lone UTF-16 surrogate (U+D800) is a legal JSON \\uXXXX escape and
    round-trips through json.loads() into a real Python str carrying an
    unpaired surrogate. It is not whitespace (passes verdict_needs_reason),
    contains no line-break character (passes verdict_string_multiline), and
    is a perfectly normal-looking string to every content gate -- it fails
    only when the report is serialised back out to UTF-8 bytes at WRITE
    time. Must surface as report_write_failed, never an unhandled
    UnicodeEncodeError, and must publish nothing and leave no temp file
    behind in stale_records/."""
    verdicts = _prepped_verdicts(root)
    verdicts["seg01"]["notes"][1]["reason"] = "\ud800"

    vdir = root / "stale_records" / "verdicts"
    vdir.mkdir(parents=True, exist_ok=True)
    for seg, doc in verdicts.items():
        # ensure_ascii=True so the file literally contains the six-character
        # escape sequence "\ud800", never a raw surrogate byte sequence
        # (which UTF-8 cannot represent at all) -- json.loads() is what
        # turns that escape back into an in-memory surrogate character.
        text = json.dumps(doc, ensure_ascii=True)
        (vdir / f"{seg}.json").write_text(text, encoding="utf-8")

    _assert_fatal(_build(root), "report_write_failed")
    assert not (root / "stale_records" / "stale_records_report.json").exists()
    assert not (root / "stale_records" / "STALE_RECORDS.md").exists()
    leftover_tmp = [p.name for p in (root / "stale_records").glob(".*") if p.is_file()]
    assert leftover_tmp == [], leftover_tmp


def test_build_report_target_not_a_file_leaves_a_pre_existing_report_untouched(root):
    """The up-front target-type check runs over BOTH targets before either
    is staged, so a directory blocking STALE_RECORDS.md is caught before
    stale_records_report.json is even opened -- a pre-existing report.json
    is untouched not because anything was rolled back, but because nothing
    was ever attempted."""
    _write_verdicts(root, _prepped_verdicts(root))
    pre_report = json.dumps({"schema_version": 1, "note": "pre-existing content X"}).encode("utf-8")
    (root / "stale_records" / "stale_records_report.json").write_bytes(pre_report)

    blocker = root / "stale_records" / "STALE_RECORDS.md"
    blocker.mkdir(parents=True, exist_ok=True)
    (blocker / "occupied").write_text("x", encoding="utf-8")

    _assert_fatal(_build(root), "report_target_not_a_file")
    assert (root / "stale_records" / "stale_records_report.json").read_bytes() == pre_report
    leftover_tmp = [p.name for p in (root / "stale_records").glob(".*") if p.is_file()]
    assert leftover_tmp == [], leftover_tmp


def test_prep_report_target_not_a_file_leaves_a_pre_existing_prep_json_untouched(root):
    """The same up-front check, `--prep` side: a directory blocking
    names_report.json is caught before prep.json is even opened."""
    pre_prep = json.dumps({"schema_version": 1, "note": "pre-existing content Y"}).encode("utf-8")
    (root / "stale_records").mkdir(parents=True, exist_ok=True)
    (root / "stale_records" / "prep.json").write_bytes(pre_prep)

    blocker = root / "stale_records" / "names_report.json"
    blocker.mkdir(parents=True, exist_ok=True)
    (blocker / "occupied").write_text("x", encoding="utf-8")

    _assert_fatal(_prep(root), "report_target_not_a_file")
    assert (root / "stale_records" / "prep.json").read_bytes() == pre_prep
    leftover_tmp = [p.name for p in (root / "stale_records").glob(".*") if p.is_file()]
    assert leftover_tmp == [], leftover_tmp


def test_prep_report_target_not_a_file_on_first_publication_leaves_no_prep_json(root):
    """A FIRST-EVER run (no prior prep.json at all) that hits a directory
    blocking names_report.json is refused by the up-front check before
    either target is staged -- neither file exists afterward, on the first
    publish too, not only on a re-run."""
    blocker = root / "stale_records" / "names_report.json"
    blocker.mkdir(parents=True, exist_ok=True)
    (blocker / "occupied").write_text("x", encoding="utf-8")

    _assert_fatal(_prep(root), "report_target_not_a_file")
    assert not (root / "stale_records" / "prep.json").exists()
    leftover_tmp = [p.name for p in (root / "stale_records").glob(".*") if p.is_file()]
    assert leftover_tmp == [], leftover_tmp


def test_build_report_target_not_a_file_on_first_publication_leaves_no_report_json(root):
    """The same first-publication case, `--build` side: no prior
    stale_records_report.json, STALE_RECORDS.md blocked."""
    _write_verdicts(root, _prepped_verdicts(root))
    blocker = root / "stale_records" / "STALE_RECORDS.md"
    blocker.mkdir(parents=True, exist_ok=True)
    (blocker / "occupied").write_text("x", encoding="utf-8")

    _assert_fatal(_build(root), "report_target_not_a_file")
    assert not (root / "stale_records" / "stale_records_report.json").exists()
    leftover_tmp = [p.name for p in (root / "stale_records").glob(".*") if p.is_file()]
    assert leftover_tmp == [], leftover_tmp


def test_draft_changed_during_read_is_a_documented_and_raised_fatal_reason():
    """A read-hash-then-read-parse race in load_segment_draft() (the draft
    file is replaced between the content-hash read and the structural read
    that follows it) cannot be forced deterministically from a subprocess
    test -- there is no seam to inject a file replacement between two reads
    inside one function call. This pins only the static contract: the
    reason is named in the module's own fatal-reasons docstring list AND is
    the reason of a real `raise RegistryError(...)` somewhere in the
    source -- named-but-never-raised would be a dead doc entry, and
    raised-but-undocumented would leave an operator with no way to look the
    reason up."""
    text = SCRIPT.read_text(encoding="utf-8")
    first_quote = text.index('"""')
    docstring_end = text.index('"""', first_quote + 3)
    module_docstring = text[first_quote:docstring_end]
    assert "draft_changed_during_read" in module_docstring, (
        "the module docstring's fatal-reasons list does not name "
        "draft_changed_during_read"
    )
    assert re.search(r'raise RegistryError\(\s*\n?\s*"draft_changed_during_read"', text), (
        "no `raise RegistryError(\"draft_changed_during_read\", ...)` found in the "
        "shipped source"
    )


# ---------------------------------------------------------------------------
# --build: the happy path, fed the REAL --prep output (the integration case).
# ---------------------------------------------------------------------------

def _run_prep_and_get_shas(root: Path) -> dict:
    proc = _prep(root)
    payload = parse_summary(proc)
    assert proc.returncode == 0, (payload, proc.stderr)
    doc = json.loads((root / "stale_records" / "prep.json").read_text(encoding="utf-8"))
    return {s["seg"]: s["draft_sha1"] for s in doc["segments"]}


def _valid_verdicts(shas: dict) -> dict:
    return {
        "seg01": {
            "schema_version": 1,
            "seg": "seg01",
            "draft_sha1": shas["seg01"],
            "notes": [
                {"index": 0, "verdict": "stale", "quoted_form": "Krimintshak",
                 "reason": "the town name was corrected after a class fix"},
                {"index": 1, "verdict": "provenance", "quoted_form": None,
                 "reason": "note preserves the historical spelling on purpose"},
                {"index": 2, "verdict": "current", "quoted_form": None, "reason": ""},
            ],
        },
        "FRONTBACK:fm01": {
            "schema_version": 1,
            "seg": "FRONTBACK:fm01",
            "draft_sha1": shas["FRONTBACK:fm01"],
            "notes": [
                {"index": 0, "verdict": "current", "quoted_form": None, "reason": ""},
            ],
        },
    }


def _write_verdicts(root: Path, verdicts: dict) -> None:
    vdir = root / "stale_records" / "verdicts"
    vdir.mkdir(parents=True, exist_ok=True)
    for seg, doc in verdicts.items():
        _write_json(vdir / f"{seg}.json", doc)


def _prepped_verdicts(root: Path) -> dict:
    """Runs --prep and returns the valid verdict set bound to its shas."""
    return _valid_verdicts(_run_prep_and_get_shas(root))


def _single_note_root(tmp_path: Path, *, blocks_text: str, note_text: str, seg: str = "segq") -> Path:
    """A minimal one-converged-segment root with ONE note and NO names[]
    rows -- isolates the verdict `quoted_form` matching behaviour from the
    shared multi-segment fixture, whose blocks/notes text is fixed by other
    tests."""
    root = tmp_path / "durable_root_single"
    _copy_scripts(root / "scripts")
    (root / "segments").mkdir(parents=True, exist_ok=True)
    (root / "runs" / "ledger.d").mkdir(parents=True, exist_ok=True)
    draft = {
        "seg": seg,
        "blocks": {f"PARA:{seg}:0001": blocks_text},
        "footnotes": {},
        "verses": {},
        "names": [],
        "notes": [note_text],
        "dispatch_token": "tok",
    }
    _write_json(root / "segments" / f"{seg}.draft.json", draft)
    _write_json(root / "runs" / "ledger.d" / f"{seg}.json",
                {"seg": seg, "status": "converged", "reviewed_draft_sha1": "0" * 40})
    return root


def _write_single_note_verdict(root: Path, seg: str, *, verdict: str, quoted_form, reason: str) -> None:
    shas = _run_prep_and_get_shas(root)
    doc = {
        "schema_version": 1, "seg": seg, "draft_sha1": shas[seg],
        "notes": [{"index": 0, "verdict": verdict, "quoted_form": quoted_form, "reason": reason}],
    }
    _write_json(root / "stale_records" / "verdicts" / f"{seg}.json", doc)


def _assert_refusal(root: Path, reason: str) -> dict:
    proc = _build(root)
    payload = parse_summary(proc)
    assert proc.returncode == 1, (payload, proc.stderr)
    assert payload["success"] is False
    assert payload["reason"] == reason, (reason, payload)
    assert not (root / "stale_records" / "stale_records_report.json").exists()
    assert not (root / "stale_records" / "STALE_RECORDS.md").exists()
    return payload


def test_build_happy_path_writes_the_report_and_the_markdown(root):
    _write_verdicts(root, _prepped_verdicts(root))
    proc = _build(root)
    payload = parse_summary(proc)
    assert proc.returncode == 0, (payload, proc.stderr)
    assert payload["success"] is True
    assert payload["mode"] == "build"
    assert payload["segments_selected"] == 2
    assert payload["names_total"] == 4
    assert payload["names_flagged"] == 1
    assert payload["names_unverifiable"] == 2
    assert payload["names_unreadable"] == 0
    assert payload["notes_total"] == 4
    assert payload["notes_stale"] == 1
    assert payload["notes_provenance"] == 1

    report_path = root / "stale_records" / "stale_records_report.json"
    md_path = root / "stale_records" / "STALE_RECORDS.md"
    assert report_path.is_file()
    assert md_path.is_file()
    assert payload["report_path"] == str(report_path)
    assert payload["markdown_path"] == str(md_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    seg_reports = {s["seg"]: s for s in report["segments"]}
    seg01_report = seg_reports["seg01"]
    notes_rows = {r["index"]: r for r in seg01_report["notes_rows"]}
    assert notes_rows[0]["verdict"] == "stale"
    assert notes_rows[0]["note"] == SEG01_NOTES[0]  # original note text copied in
    assert notes_rows[1]["verdict"] == "provenance"
    assert notes_rows[1]["note"] == SEG01_NOTES[1]
    assert 2 not in notes_rows, "a 'current' row must not appear in the report"

    names_rows = {r["index"]: r for r in seg01_report["names_rows"]}
    assert names_rows[0]["source_form"] == "town_x"
    assert names_rows[0]["missing_tokens"] == ["Krimintshuk"]
    fm01_report = seg_reports["FRONTBACK:fm01"]
    assert fm01_report["names_rows"] == []  # unverifiable rows are counted, never itemised
    assert fm01_report["names_unverifiable"] == 1
    assert {r["index"] for r in fm01_report["notes_rows"]} == set()  # its one note is "current"


def test_build_happy_path_markdown_names_and_folds_the_multiline_note(root):
    _write_verdicts(root, _prepped_verdicts(root))
    assert _build(root).returncode == 0

    md = (root / "stale_records" / "STALE_RECORDS.md").read_text(encoding="utf-8")
    assert "seg01" in md
    assert "FRONTBACK:fm01" in md
    assert ("- names[0] " + "\u2039" + "town_x" + "\u203a" + " " + "\u2192" + " " + "\u2039" + "in Krimintshuk" + "\u203a" + " " + "\u2014" + " missing: Krimintshuk") in md
    assert ("- notes[0] STALE " + "\u00ab" + "Krimintshak" + "\u00bb" + " " + "\u2014" + " the town name was corrected after a class fix") in md
    assert ("- notes[1] PROVENANCE " + "\u2014" + " note preserves the historical spelling on purpose") in md
    # notes[0]'s note text carries a REAL embedded newline; the rendered
    # "  > note" line must fold it to one physical line, not two.
    folded = "  > The town was earlier rendered as \"Krimintshak\" in the draft."
    assert folded in md.splitlines(), md
    assert not any(line.strip() == "rendered as \"Krimintshak\" in the draft."
                   for line in md.splitlines())


def test_build_markdown_shows_counts_never_clean_for_an_unverifiable_only_segment(root):
    """FRONTBACK:fm01 has zero FLAGGED names, zero unreadable names, and its
    one note is "current" -- exactly the shape a shallower "(clean)" gate
    used to treat as "nothing to look at". Its single UNVERIFIABLE names row
    must still surface as a counts line, never "(clean)"."""
    _write_verdicts(root, _prepped_verdicts(root))
    assert _build(root).returncode == 0

    md = (root / "stale_records" / "STALE_RECORDS.md").read_text(encoding="utf-8")
    lines = md.splitlines()
    heading_idx = lines.index("## FRONTBACK:fm01")
    following = next(line for line in lines[heading_idx + 1:] if line.strip())
    assert following == (
        "names: 1 total, 0 flagged, 1 unverifiable, 0 unreadable "
        "\u00b7 notes: 1 total, 0 stale, 0 provenance"
    )
    section_end = next(
        (i for i in range(heading_idx + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    assert "(clean)" not in lines[heading_idx:section_end]


def _heading_line_count(md_text: str) -> int:
    return sum(1 for line in md_text.splitlines() if line.startswith("## "))


def test_build_a_names_target_form_cannot_forge_a_markdown_heading(root):
    """A flagged canonical_target_form is schema-unconstrained free text this
    script never gates (see module docstring); `inline_md` must still fold
    any embedded newline before it reaches STALE_RECORDS.md, or a corrected
    class of renderings could inject a fake `## ` section into the report."""
    _write_verdicts(root, _prepped_verdicts(root))
    assert _build(root).returncode == 0
    baseline_headings = _heading_line_count(
        (root / "stale_records" / "STALE_RECORDS.md").read_text(encoding="utf-8"))

    draft_path = root / "segments" / "seg01.draft.json"
    doc = json.loads(draft_path.read_text(encoding="utf-8"))
    doc["names"][0]["canonical_target_form"] += "\n## seg99"
    draft_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    for rel in ("stale_records/prep.json", "stale_records/names_report.json",
                "stale_records/stale_records_report.json", "stale_records/STALE_RECORDS.md"):
        (root / rel).unlink(missing_ok=True)

    shas2 = _run_prep_and_get_shas(root)
    _write_verdicts(root, _valid_verdicts(shas2))
    proc = _build(root)
    payload = parse_summary(proc)
    assert proc.returncode == 0, (payload, proc.stderr)

    md = (root / "stale_records" / "STALE_RECORDS.md").read_text(encoding="utf-8")
    assert _heading_line_count(md) == baseline_headings
    assert not any("seg99" in line for line in md.splitlines() if line.startswith("## "))


# ---------------------------------------------------------------------------
# --build: one executable case per named refusal. Each corrupts exactly one
# invariant of an otherwise-valid pair of verdicts fed the REAL prep sha, so
# the reason asserted is the one gate the corruption actually violates.
# ---------------------------------------------------------------------------

def test_build_refuses_unreadable_verdict_json_and_leaves_a_pre_existing_report_untouched(root):
    verdicts = _prepped_verdicts(root)
    vdir = root / "stale_records" / "verdicts"
    vdir.mkdir(parents=True, exist_ok=True)
    _write_json(vdir / "FRONTBACK:fm01.json", verdicts["FRONTBACK:fm01"])
    (vdir / "seg01.json").write_text("not { valid json", encoding="utf-8")

    pre_report = json.dumps({"schema_version": 1, "note": "an earlier build"}).encode("utf-8")
    pre_md = b"# earlier STALE_RECORDS.md\n"
    (root / "stale_records" / "stale_records_report.json").write_bytes(pre_report)
    (root / "stale_records" / "STALE_RECORDS.md").write_bytes(pre_md)

    proc = _build(root)
    payload = parse_summary(proc)
    assert proc.returncode == 1, (payload, proc.stderr)
    assert payload["success"] is False
    assert payload["reason"] == "verdict_unreadable"
    assert (root / "stale_records" / "stale_records_report.json").read_bytes() == pre_report
    assert (root / "stale_records" / "STALE_RECORDS.md").read_bytes() == pre_md


def test_build_refuses_a_verdict_file_that_is_not_valid_utf8(root):
    """`Path.read_text(encoding="utf-8")` raises UnicodeDecodeError on
    invalid UTF-8 bytes -- a ValueError subclass, NOT an OSError subclass --
    so this is a distinct case from the malformed-JSON one above: it proves
    the read's except clause actually catches the decode failure too,
    rather than letting it escape as an unhandled traceback."""
    verdicts = _prepped_verdicts(root)
    vdir = root / "stale_records" / "verdicts"
    vdir.mkdir(parents=True, exist_ok=True)
    _write_json(vdir / "FRONTBACK:fm01.json", verdicts["FRONTBACK:fm01"])
    (vdir / "seg01.json").write_bytes(b"\xff\xfe{")
    _assert_refusal(root, "verdict_unreadable")


def test_build_refuses_a_verdict_file_with_pathologically_deep_json_nesting(root):
    verdicts = _prepped_verdicts(root)
    vdir = root / "stale_records" / "verdicts"
    vdir.mkdir(parents=True, exist_ok=True)
    _write_json(vdir / "FRONTBACK:fm01.json", verdicts["FRONTBACK:fm01"])
    (vdir / "seg01.json").write_text(_DEEP_NESTING, encoding="utf-8")
    _assert_refusal(root, "verdict_unreadable")


def test_build_refuses_a_verdict_missing_a_required_key(root):
    verdicts = _prepped_verdicts(root)
    del verdicts["seg01"]["draft_sha1"]
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "verdict_shape")


def test_build_refuses_a_verdict_whose_notes_field_is_not_an_array(root):
    verdicts = _prepped_verdicts(root)
    verdicts["seg01"]["notes"] = {"index": 0, "verdict": "current", "quoted_form": None, "reason": ""}
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "verdict_shape")


def test_build_refuses_a_verdict_whose_note_index_is_not_an_integer(root):
    verdicts = _prepped_verdicts(root)
    verdicts["seg01"]["notes"][0]["index"] = "0"
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "verdict_shape")


def test_build_refuses_a_verdict_with_schema_version_not_equal_to_1(root):
    verdicts = _prepped_verdicts(root)
    verdicts["seg01"]["schema_version"] = 2
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "verdict_shape")


def test_build_refuses_a_verdict_with_an_empty_string_quoted_form(root):
    """quoted_form must be a non-empty string or null -- never "" -- because
    an empty quoted_form is meaningless input to both downstream checks
    (quoted_form_not_in_note, stale_form_present_in_blocks) that treat a
    non-null quoted_form as "the verdict is quoting something specific"."""
    verdicts = _prepped_verdicts(root)
    verdicts["seg01"]["notes"][1]["quoted_form"] = ""
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "verdict_shape")


def test_build_refuses_a_verdict_with_an_unknown_key(root):
    verdicts = _prepped_verdicts(root)
    verdicts["seg01"]["bogus_extra_key"] = "x"
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "verdict_extra_keys")


def test_build_refuses_a_verdict_for_a_segment_prep_never_selected(root):
    verdicts = _prepped_verdicts(root)
    verdicts["ghost"] = {
        "schema_version": 1, "seg": "ghost", "draft_sha1": "0" * 40, "notes": [],
    }
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "verdict_seg_unknown")


def test_build_refuses_a_verdict_whose_filename_disagrees_with_its_own_seg(root):
    shas = _run_prep_and_get_shas(root)
    valid = _valid_verdicts(shas)
    vdir = root / "stale_records" / "verdicts"
    vdir.mkdir(parents=True, exist_ok=True)
    # Filename stem "seg01" but the body's own `seg` names FRONTBACK:fm01 --
    # a real, known segment, so this is a pure filename/body mismatch and not
    # also a verdict_seg_unknown.
    _write_json(vdir / "seg01.json", valid["FRONTBACK:fm01"])
    _write_json(vdir / "FRONTBACK:fm01.json", valid["FRONTBACK:fm01"])
    proc = _build(root)
    payload = parse_summary(proc)
    assert proc.returncode == 1, (payload, proc.stderr)
    assert payload["success"] is False
    assert payload["reason"] == "verdict_file_seg_mismatch"
    assert not (root / "stale_records" / "stale_records_report.json").exists()


def test_build_refuses_when_the_draft_was_edited_after_the_verdict_was_judged(root):
    """The one surviving verdict_stale_draft case (round 2): --build
    recomputes the draft's content hash straight off disk and compares it
    to the verdict's own claimed draft_sha1 -- there is no separate "prep's
    sha" to disagree with any more, only the verdict-vs-disk comparison."""
    shas = _run_prep_and_get_shas(root)
    _write_verdicts(root, _valid_verdicts(shas))  # self-consistent with the draft on disk

    draft_path = root / "segments" / "seg01.draft.json"
    doc = json.loads(draft_path.read_text(encoding="utf-8"))
    doc["notes"].append("Added after judging, never seen by the judge.")
    draft_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    _assert_refusal(root, "verdict_stale_draft")


def test_build_refuses_a_verdict_with_a_coverage_hole(root):
    verdicts = _prepped_verdicts(root)
    verdicts["seg01"]["notes"] = [verdicts["seg01"]["notes"][0], verdicts["seg01"]["notes"][2]]
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "verdict_coverage")


def test_build_refuses_a_verdict_with_a_duplicate_index(root):
    verdicts = _prepped_verdicts(root)
    verdicts["seg01"]["notes"] = [verdicts["seg01"]["notes"][0], verdicts["seg01"]["notes"][0]]
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "verdict_coverage")


def test_build_refuses_a_stale_verdict_with_no_reason(root):
    verdicts = _prepped_verdicts(root)
    verdicts["seg01"]["notes"][0]["reason"] = "   "
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "verdict_needs_reason")


def test_build_refuses_a_provenance_verdict_with_no_reason(root):
    verdicts = _prepped_verdicts(root)
    verdicts["seg01"]["notes"][1]["reason"] = ""
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "verdict_needs_reason")


def test_build_refuses_a_multiline_reason(root):
    verdicts = _prepped_verdicts(root)
    verdicts["seg01"]["notes"][2]["verdict"] = "provenance"
    verdicts["seg01"]["notes"][2]["reason"] = "line one\nline two"
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "verdict_string_multiline")


def test_build_refuses_a_multiline_quoted_form(root):
    verdicts = _prepped_verdicts(root)
    # notes[0]'s real text is "The town was earlier\nrendered as ...", so
    # this quoted_form both spans the note's own embedded newline AND stays
    # a genuine substring of it -- isolating the multiline gate from
    # quoted_form_not_in_note.
    verdicts["seg01"]["notes"][0]["quoted_form"] = "earlier\nrendered"
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "verdict_string_multiline")


def test_build_refuses_a_quoted_form_the_note_does_not_contain(root):
    verdicts = _prepped_verdicts(root)
    verdicts["seg01"]["notes"][0]["quoted_form"] = "Nonexistent Form"
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "quoted_form_not_in_note")


def test_build_refuses_a_stale_quoted_form_still_present_in_the_blocks(root):
    verdicts = _prepped_verdicts(root)
    # notes[2]'s text ("...arrives in Uman by cart.") genuinely contains
    # "Uman", and "Uman" is genuinely in the blocks -- isolating this gate
    # from quoted_form_not_in_note.
    verdicts["seg01"]["notes"][2]["verdict"] = "stale"
    verdicts["seg01"]["notes"][2]["quoted_form"] = "Uman"  # a single WHOLE token of the blocks
    verdicts["seg01"]["notes"][2]["reason"] = "the form is still present, contradicting staleness"
    _write_verdicts(root, verdicts)
    _assert_refusal(root, "stale_form_present_in_blocks")


def test_build_accepts_a_stale_quoted_form_that_is_only_a_substring_of_a_block_token(tmp_path):
    """The SAME whole-token discipline the names[] predicate already uses
    (Odes/Odessa, 1.132.0) also governs stale_form_present_in_blocks:
    "Odes" is a substring of "Odessa" but never a whole token of it, so a
    verdict correctly marking "Odes" stale (retired, replaced by "Odessa")
    must NOT be refused as self-contradictory."""
    root = _single_note_root(
        tmp_path, blocks_text="He now lived in Odessa.",
        note_text="The town was earlier called Odes in the draft.")
    _write_single_note_verdict(
        root, "segq", verdict="stale", quoted_form="Odes",
        reason="the town was retired in favour of Odessa")
    proc = _build(root)
    payload = parse_summary(proc)
    assert proc.returncode == 0, (payload, proc.stderr)


def test_build_refuses_a_multiword_stale_quoted_form_present_as_a_contiguous_token_run(tmp_path):
    """A multi-word quoted_form tokenizes to a SEQUENCE, and the gate must
    check for that exact contiguous run in the blocks' own token sequence --
    "R. Noson" tokenizes to ("R", "Noson"), and blocks containing that pair
    adjacent and in order really do still carry the quoted form."""
    root = _single_note_root(
        tmp_path, blocks_text="He met R. Noson at the market.",
        note_text="The elder was earlier named R. Noson in this segment.")
    _write_single_note_verdict(
        root, "segq", verdict="stale", quoted_form="R. Noson",
        reason="the honorific was dropped")
    proc = _build(root)
    payload = parse_summary(proc)
    assert proc.returncode == 1, (payload, proc.stderr)
    assert payload["reason"] == "stale_form_present_in_blocks"


def test_build_accepts_a_multiword_stale_quoted_form_whose_tokens_appear_out_of_order(tmp_path):
    """The other side of the same contiguous-run check: both tokens of "R.
    Noson" are individually present in blocks reading "Noson R", but never
    adjacent IN THAT ORDER -- a token-set check would wrongly treat this as
    still present; the sequence check must not."""
    root = _single_note_root(
        tmp_path, blocks_text="He met Noson R at the market.",
        note_text="The elder was earlier named R. Noson in this segment.")
    _write_single_note_verdict(
        root, "segq", verdict="stale", quoted_form="R. Noson",
        reason="the honorific was dropped")
    proc = _build(root)
    payload = parse_summary(proc)
    assert proc.returncode == 0, (payload, proc.stderr)


def test_build_refuses_a_quoted_form_with_no_letters(tmp_path):
    """A punctuation-only quoted_form ("?!") has no token content at all --
    a contiguous-token-run check against it would be meaningless, so this is
    refused at the shape gate rather than silently accepted or vacuously
    matched."""
    root = _single_note_root(
        tmp_path, blocks_text="Irrelevant blocks text.",
        note_text="The note quotes the symbol ?! as an example.")
    _write_single_note_verdict(
        root, "segq", verdict="stale", quoted_form="?!",
        reason="a punctuation-only quoted form")
    proc = _build(root)
    payload = parse_summary(proc)
    assert proc.returncode == 1, (payload, proc.stderr)
    assert payload["reason"] == "verdict_shape"


def test_build_refuses_when_a_selected_segment_has_no_verdict_file(root):
    verdicts = _prepped_verdicts(root)
    del verdicts["FRONTBACK:fm01"]
    _write_verdicts(root, verdicts)
    payload = _assert_refusal(root, "verdict_missing")
    assert "FRONTBACK:fm01" in payload.get("missing_segments", [])


def test_build_refuses_when_the_verdicts_directory_is_unreadable(root):
    """Listing an unreadable stale_records/verdicts/ (glob("*.json") over a
    directory the process cannot read) is a could-not-look, not a
    nobody-wrote-verdicts-yet -- the same ledger_d_unreadable-vs-absent
    split, one directory over. Fatal (exit 2), never conflated with
    verdict_missing (which means the directory WAS readable and simply
    lacked a file)."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses permission bits")
    _write_verdicts(root, _prepped_verdicts(root))
    vdir = root / "stale_records" / "verdicts"
    vdir.chmod(0o000)
    try:
        try:
            list(vdir.iterdir())
        except PermissionError:
            pass
        else:
            pytest.skip("this environment does not enforce directory permission bits")
        _assert_fatal(_build(root), "verdicts_dir_unreadable")
    finally:
        vdir.chmod(0o755)


# ---------------------------------------------------------------------------
# The names[] predicate, in-process. Not the population selection above --
# the pure per-row evaluation, called directly on the loaded module so each
# boundary case is pinned without a full prep run.
# ---------------------------------------------------------------------------

def test_classify_name_entry_nfc_normalizes_before_tokenizing():
    composed = {"source_form": "s", "canonical_target_form": "\u00c9lie"}  # NFC
    decomposed = {"source_form": "s", "canonical_target_form": "E\u0301lie"}  # NFD
    status_c, row_c = mod.classify_name_entry(composed)
    status_d, row_d = mod.classify_name_entry(decomposed)
    assert status_c == status_d == "candidate"
    assert row_c["_qualifying_tokens"] == row_d["_qualifying_tokens"]


def test_classify_name_entry_ignores_a_bare_initial():
    status, row = mod.classify_name_entry({"source_form": "s", "canonical_target_form": "R. Noson"})
    assert status == "candidate"
    assert "R" not in row["_qualifying_tokens"]
    assert "Noson" in row["_qualifying_tokens"]


def _tokens(text: str) -> set:
    """block_token_set(nfc(text)) -- the WHOLE-TOKEN set evaluate_name_entry
    checks a target's qualifying tokens against (1.132.0: a substring check
    was measured to miss "Odes" -> "Odessa", a retired form that is a strict
    prefix of its replacement -- 72/85 recall vs 84/85 for whole-token
    membership; see the CHANGELOG and evaluate_name_entry's own docstring)."""
    return mod.block_token_set(mod.nfc(text))


def test_evaluate_name_entry_nfc_normalizes_a_composed_target_against_decomposed_blocks():
    entry = {"source_form": "s", "canonical_target_form": "\u00c9lie"}  # NFC
    status, row = mod.evaluate_name_entry(entry, _tokens("Il a vu E\u0301lie hier."), 0)
    assert status == "clean"
    assert row is None


def test_evaluate_name_entry_nfc_normalizes_a_decomposed_target_against_composed_blocks():
    entry = {"source_form": "s", "canonical_target_form": "E\u0301lie"}  # NFD
    status, row = mod.evaluate_name_entry(entry, _tokens("Il a vu \u00c9lie hier."), 0)
    assert status == "clean"
    assert row is None


def test_evaluate_name_entry_a_possessive_apostrophe_splits_off_the_bare_token():
    """Whole-token membership, not substring: "Hirsch\u2019s" tokenizes to
    {"Hirsch", "s"} (the apostrophe is not a word character), so the bare
    target "Hirsch" is still a set member even though it is never itself a
    "\\n"-free standalone word in the prose on its own."""
    entry = {"source_form": "s", "canonical_target_form": "Hirsch"}
    status, row = mod.evaluate_name_entry(entry, _tokens("This was Hirsch\u2019s house."), 0)
    assert status == "clean"


def test_evaluate_name_entry_a_retired_form_that_is_a_prefix_of_its_replacement_is_flagged():
    """The regression case the whole-token predicate exists for: "Odes" is a
    SUBSTRING of "Odessa" but never a whole token of it, so a corrected book
    (old form retired, replaced by "Odessa") must still flag it."""
    entry = {"source_form": "s", "canonical_target_form": "from Odes"}
    status, row = mod.evaluate_name_entry(entry, _tokens("He now lived in Odessa."), 0)
    assert status == "flagged"
    assert row["missing_tokens"] == ["Odes"]


def test_evaluate_name_entry_treats_a_bare_initial_target_as_unverifiable():
    entry = {"source_form": "s", "canonical_target_form": "R."}
    status, row = mod.evaluate_name_entry(
        entry, _tokens("Some blocks text without a match anywhere in it."), 0)
    assert status == "unverifiable"
    assert row is None


def test_evaluate_name_entry_all_lowercase_target_is_unverifiable():
    entry = {"source_form": "s", "canonical_target_form": "without end"}
    status, row = mod.evaluate_name_entry(entry, _tokens("irrelevant blocks text"), 0)
    assert status == "unverifiable"
    assert row is None


def test_evaluate_name_entry_missing_target_field_is_unreadable():
    entry = {"source_form": "s"}
    status, row = mod.evaluate_name_entry(entry, _tokens("irrelevant"), 0)
    assert status == "unreadable"
    assert row is None


def test_evaluate_name_entry_accepts_the_target_form_field_convention():
    entry = {"source_form": "s", "target_form": "Uman"}
    status, row = mod.evaluate_name_entry(entry, _tokens("He went to Uman."), 0)
    assert status == "clean"


# A Hebrew SOURCE form beside a Latin target form is the ordinary shape for a
# Hebrew source book (see literary-translator-run), never a corner case: the
# predicate is capitalisation-based and Hebrew has no case distinction, but
# it only ever looks at the TARGET form's tokens, so the source form's script
# must not matter to the verdict.
HEBREW_SOURCE_FORM = "\u05de\u05e9\u05d4"  # "Moshe", written by \u escapes, never pasted


def test_evaluate_name_entry_handles_a_hebrew_source_form():
    entry = {"source_form": HEBREW_SOURCE_FORM, "canonical_target_form": "Moshe"}
    status, row = mod.evaluate_name_entry(entry, _tokens("He met Moshe at the market."), 0)
    assert status == "clean"


def test_evaluate_name_entry_flags_when_the_target_is_absent_from_blocks():
    entry = {"source_form": "s", "canonical_target_form": "in Krimintshuk"}
    status, row = mod.evaluate_name_entry(entry, _tokens(SEG01_BLOCK_TEXT), 3)
    assert status == "flagged"
    assert row["missing_tokens"] == ["Krimintshuk"]
    assert row["index"] == 3


# ---------------------------------------------------------------------------
# The template and the judge's agent definition.
# ---------------------------------------------------------------------------

def test_template_exists_and_names_the_contract():
    assert TEMPLATE.is_file()
    text = TEMPLATE.read_text(encoding="utf-8")
    for word in ("stale", "provenance", "current"):
        assert word in text, word
    assert "draft_sha1" in text
    assert "blocks" in text
    assert '"schema_version": 1' in text
    assert "never an empty string" in text
    # binds the verdict to a CONTENT HASH, not literal on-disk bytes --
    # draft_sha1.draft_content_sha1 excludes dispatch_token, so "exact
    # bytes" would misdescribe what the judge is actually being bound to.
    assert "content hash" in text
    assert "exact draft bytes" not in text


def test_stale_notes_judge_agent_definition_is_shipped():
    assert AGENT_DEF.is_file()


def test_stale_notes_judge_tool_allowlist_is_exactly_read():
    fields = read_frontmatter(AGENT_DEF)
    assert "tools" in fields
    assert tool_allowlist(fields) == ["Read"]


def test_stale_notes_judge_declares_the_name_the_dispatch_uses():
    fields = read_frontmatter(AGENT_DEF)
    assert fields.get("name") == AGENT_DEF.stem


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
