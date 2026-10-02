"""Provider counter semantics and fail-closed CLI accounting for #962."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = (Path(__file__).resolve().parents[1] / "skills/literary-translator"
          / "assets/scripts/worker_usage.py")


def invoke(tmp_path, fmt, rows, extra_records=()):
    record = tmp_path / "record.jsonl"
    record.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--format", fmt, "--worker-class", "review",
         str(record), *map(str, extra_records)], capture_output=True, text=True)
    return proc, record


def claude(mid="m1", **overrides):
    usage = dict(input_tokens=2, cache_creation_input_tokens=30,
                 cache_read_input_tokens=70, output_tokens=5)
    usage.update(overrides)
    return {"type": "assistant", "message": {
        "id": mid, "model": "claude-test", "usage": usage,
        "content": [{"type": "text", "text": "SECRET SOURCE TEXT"}]}}


def codex(input_tokens=100, cached=60, output=10):
    return {"type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": dict(input_tokens=input_tokens,
                                  cached_input_tokens=cached, output_tokens=output)}}}


def test_claude_counts_cache_input_and_deduplicates_streamed_blocks(tmp_path):
    proc, record = invoke(tmp_path, "claude-session",
                          [claude(), claude(output_tokens=8), claude("m2")])
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    row = result["records"][0]
    assert row["observations"] == 2
    assert row["first_observation"]["input_tokens"] == 102
    assert row["first_observation"]["output_tokens"] == 8
    assert row["total_observed"] == dict(input_tokens=204, cached_input_tokens=140,
                                         cache_creation_input_tokens=60,
                                         uncached_input_tokens=4, output_tokens=13)
    assert row["sha256"] == hashlib.sha256(record.read_bytes()).hexdigest()
    assert row["attribution"] == "unavailable"
    assert "SECRET SOURCE TEXT" not in proc.stdout
    assert len(proc.stdout.splitlines()) == 1


def test_codex_cumulative_notifications_are_deltas_not_repeated_charges(tmp_path):
    proc, _ = invoke(tmp_path, "codex-session", [
        codex(), codex(),
        {"type": "event_msg", "payload": {"type": "token_count", "info": None}},
        codex(250, 160, 40), codex(250, 160, 40)])
    assert proc.returncode == 0, proc.stderr
    row = json.loads(proc.stdout)["records"][0]
    assert row["observation_unit"] == "cumulative_usage_delta"
    assert row["observations"] == 2
    assert row["total_observed"] == dict(input_tokens=250, cached_input_tokens=160,
                                         output_tokens=40)


def test_exec_turn_usage_is_not_labeled_per_request(tmp_path):
    proc, _ = invoke(tmp_path, "codex-exec", [
        {"type": "turn.completed", "usage": dict(input_tokens=900,
                                                 cached_input_tokens=500,
                                                 output_tokens=100)},
        {"type": "turn.failed", "error": {"message": "quota"}}])
    assert proc.returncode == 0
    row = json.loads(proc.stdout)["records"][0]
    assert row["observation_unit"] == "completed_turn"
    assert row["scope"] == "observed_usage_only_not_proof_of_job_completion"


@pytest.mark.parametrize("fmt,rows,reason", [
    ("claude-session", [claude(), claude(cache_read_input_tokens=71)], "conflicting input"),
    ("claude-session", [{"type": "assistant", "message": {"usage": {}}}], "message id"),
    ("claude-session", [claude(input_tokens=True)], "non-negative integer"),
    ("claude-session", [claude(cache_creation_input_tokens=None)], "non-negative integer"),
    ("codex-session", [codex(), codex(90)], "decreased"),
    ("codex-session", [codex(50, 60)], "exceeds input"),
    ("codex-session", [codex(), codex(150, 120)], "delta exceeds"),
    ("codex-exec", [{"type": "turn.completed", "usage": {}}], "non-negative integer"),
    ("codex-exec", [{"type": "turn.failed"}], "no measured usage"),
    ("codex-session", [42], "expected an object"),
])
def test_invalid_or_unmeasured_records_emit_no_success_json(tmp_path, fmt, rows, reason):
    proc, _ = invoke(tmp_path, fmt, rows)
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert reason in proc.stderr


def test_duplicate_profile_aliases_are_not_a_larger_sample(tmp_path):
    alias = tmp_path / "alias.jsonl"
    alias.symlink_to(tmp_path / "record.jsonl")
    proc, _ = invoke(tmp_path, "claude-session", [claude()], [alias])
    assert proc.returncode == 2
    assert not proc.stdout
    assert "duplicate record" in proc.stderr


def test_synthetic_error_message_is_not_a_zero_input_request(tmp_path):
    synthetic = claude("error")
    synthetic["message"]["model"] = "<synthetic>"
    proc, _ = invoke(tmp_path, "claude-session", [synthetic])
    assert proc.returncode == 2
    assert "no measured usage" in proc.stderr


@pytest.mark.parametrize("fmt,valid,bad", [
    ("claude-session", claude(), {"type": "assistant", "message": None}),
    ("claude-session", claude(), {"type": "assistant", "message": []}),
    ("claude-session", claude(), {"type": "assistant"}),
    ("claude-session", claude(), {"type": "assistant", "message": {"id": "m2"}}),
    ("codex-session", codex(), {"type": "event_msg", "payload": None}),
    ("codex-session", codex(), {"type": "event_msg", "payload": []}),
    ("codex-session", codex(), {"type": "event_msg"}),
    ("codex-session", codex(), {"type": "event_msg", "payload": {}}),
    ("codex-session", codex(), {"type": "event_msg", "payload": {"type": "token_count"}}),
    ("codex-session", codex(), {"type": "turn_context", "payload": None}),
    ("claude-session", claude(), {}),
    ("codex-session", codex(), {"type": None}),
    ("codex-exec", {"type": "turn.completed", "usage": {
        "input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 10}}, {"type": ""}),
])
def test_malformed_row_never_produces_partial_success(tmp_path, fmt, valid, bad):
    proc, _ = invoke(tmp_path, fmt, [valid, bad])
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "worker-usage:" in proc.stderr


def test_legitimate_non_usage_notifications_can_follow_measured_usage(tmp_path):
    proc, _ = invoke(tmp_path, "codex-session", [codex(),
        {"type": "event_msg", "payload": {"type": "task_complete"}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": None}}])
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["records"][0]["observations"] == 1
    synthetic = {"type": "assistant", "message": {"model": "<synthetic>"}}
    proc, _ = invoke(tmp_path, "claude-session", [claude(), synthetic])
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["records"][0]["observations"] == 1


def test_invalid_json_missing_file_and_wrong_format_are_fatal(tmp_path):
    record = tmp_path / "bad.jsonl"
    record.write_text("{\n", encoding="utf-8")
    for path in (record, tmp_path / "missing"):
        proc = subprocess.run([sys.executable, str(SCRIPT), "--format", "codex-exec",
                               "--worker-class", "translate", str(path)],
                              capture_output=True, text=True)
        assert proc.returncode == 2
        assert not proc.stdout
    proc, _ = invoke(tmp_path, "codex-exec", [claude()])
    assert proc.returncode == 2
    assert "no measured usage" in proc.stderr
