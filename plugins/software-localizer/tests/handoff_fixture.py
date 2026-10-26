"""Synthetic native-session records for canned model turns in seam tests.

These are test inputs, never evidence of a live model run. Production capture
must read the real parent's JSONL instead of reconstructing it from these files.
"""

import json
from pathlib import Path

import packet_handoff


def record_agent_turn(run_dir, output_path, request=None, result=None, wire_input=None, completion=None):
    run_dir, output_path = Path(run_dir), Path(output_path)
    packet = json.loads((run_dir / "packet.json").read_text(encoding="utf-8"))
    if packet["kind"] not in packet_handoff.GUARDED_KINDS:
        return None
    if request is None:
        request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    if result is None:
        result = output_path.read_text(encoding="utf-8")
    tool_id = "toolu_synthetic_fixture"
    rows = [
        {"type": "assistant", "sessionId": "synthetic-session", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tool_id, "name": "Agent", "input": request}]}},
        {"type": "user", "sessionId": "synthetic-session", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": result}]}},
    ]
    if wire_input is not None:
        rows[0]["wireToolInputs"] = {tool_id: wire_input}
    if completion is not None:
        rows[1]["toolUseResult"] = completion
    transcript = run_dir / "synthetic-session.jsonl"
    transcript.write_text("".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows), encoding="utf-8")
    return packet_handoff.capture(run_dir, output_path, transcript, tool_id)
