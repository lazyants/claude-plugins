"""Verify native Claude Agent transport, independently of output/value checks.

The trusted parent supplies its real Claude session JSONL, never a reconstructed
prompt. Children need no access to files. Receipts retain only the selected
request/result records, and acceptance replays their checks rather than trusting
a saved `verified` flag. This proves transport, not linguistic/source correctness.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import uuid

import lz_common

GUARDED_KINDS = ("canon", "research", "research_verify", "review", "audit")
RESEARCH_AGENT = "software-localizer:usage-research"


class HandoffError(ValueError):
    pass


def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise HandoffError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def _constant(value):
    raise HandoffError(f"non-JSON constant: {value}")


DECODER = json.JSONDecoder(object_pairs_hook=_pairs, parse_constant=_constant)


def _json(text):
    try:
        return DECODER.decode(text)
    except (ValueError, UnicodeError) as exc:
        raise HandoffError(f"invalid JSON: {exc}") from exc


def _read_json(path):
    try:
        return _json(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise HandoffError(f"cannot read {path}: {exc}") from exc


def _digest(obj):
    # Canonical serialization preserves JSON types (true is not 1), codepoints,
    # all string whitespace, and list order; only object order/escaping disappear.
    return lz_common.sha256_json(obj)


def agent_request(kind, batch, prompt):
    return {"description": f"Localizer {kind}: {batch}", "prompt": prompt,
            "subagent_type": RESEARCH_AGENT if kind in ("research", "research_verify") else "general-purpose"}


def _select_records(transcript, tool_use_id):
    requests, results = [], []
    session_ids = set()
    try:
        with Path(transcript).open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = _json(line)
                message = row.get("message") or {}
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if (row.get("type") == "assistant" and message.get("role") == "assistant"
                            and block.get("type") == "tool_use" and block.get("id") == tool_use_id):
                        selected = {"request": block}
                        wire = row.get("wireToolInputs")
                        if wire is not None:
                            if not isinstance(wire, dict):
                                raise HandoffError("invalid native wireToolInputs")
                            if tool_use_id in wire:
                                selected["wire_input"] = wire[tool_use_id]
                        requests.append(selected)
                        session_ids.add(row.get("sessionId"))
                    if (row.get("type") == "user" and message.get("role") == "user"
                            and block.get("type") == "tool_result" and block.get("tool_use_id") == tool_use_id):
                        results.append(block)
                        session_ids.add(row.get("sessionId"))
    except (OSError, UnicodeError, AttributeError, TypeError) as exc:
        raise HandoffError(f"cannot read native session JSONL: {exc}") from exc
    # Identical duplicate streaming records are harmless; conflicting ones are not.
    requests = list({_digest(row): row for row in requests}.values())
    results = list({_digest(row): row for row in results}.values())
    if len(requests) != 1 or len(results) != 1:
        raise HandoffError("need exactly one completed Agent request/result pair for tool_use_id")
    if len(session_ids) != 1 or not all(isinstance(s, str) and s for s in session_ids):
        raise HandoffError("request and result must belong to one native session")
    return {"session_id": session_ids.pop(), **requests[0], "result": results[0]}


def _result_json(result):
    if result.get("is_error"):
        raise HandoffError("Agent returned an error")
    content = result.get("content")
    if isinstance(content, list):
        if not content or any(not isinstance(b, dict) or b.get("type") != "text"
                              or not isinstance(b.get("text"), str) for b in content):
            raise HandoffError("unsupported Agent result content")
        content = "\n".join(b["text"] for b in content)
    if not isinstance(content, str):
        raise HandoffError("Agent result has no text")
    text = content.strip()
    if text.startswith("```json\n") or text.startswith("```\n"):
        opening = text.index("\n") + 1
        closing = text.find("\n```", opening)
        if closing == -1:
            raise HandoffError("unclosed JSON answer fence")
        answer = _json(text[opening:closing])
        tail = text[closing + 4:].strip()
    else:
        try:
            answer, end = DECODER.raw_decode(text)
        except ValueError as exc:
            raise HandoffError(f"Agent answer is not JSON: {exc}") from exc
        tail = text[end:].strip()
    # Claude adds its own resumption/usage metadata after the answer. No arbitrary
    # prefix/prose or second answer is accepted as evidence of the saved result.
    metadata = (r"(?:agentId: [A-Za-z0-9_-]+(?: \((?:for resuming to continue this agent's work if needed"
                r"|use to resume this agent if needed)\))?(?:\r?\n|$))?"
                r"(?:<usage>\s*total_tokens: [0-9]+\s+tool_uses: [0-9]+"
                r"\s+duration_ms: [0-9]+\s*</usage>)?")
    if tail and not re.fullmatch(metadata, tail):
        raise HandoffError("unexpected text after Agent JSON answer")
    if not isinstance(answer, dict):
        raise HandoffError("Agent answer must be a JSON object")
    return answer


def _check(run_dir, output_path, evidence):
    packet = _read_json(run_dir / "packet.json")
    parts = _read_json(run_dir / "instructions.json")
    if not all(isinstance(obj, dict) for obj in (packet, parts, evidence)):
        raise HandoffError("packet, instructions and evidence must be JSON objects")
    if packet.get("kind") not in GUARDED_KINDS:
        raise HandoffError("receipt capture supports native Claude packet kinds only")
    prefix, suffix = parts["prefix"], parts["suffix"]
    if not isinstance(prefix, str) or not isinstance(suffix, str):
        raise HandoffError("instruction prefix and suffix must be strings")
    expected_prompt = prefix + json.dumps(packet, indent=2, sort_keys=True, ensure_ascii=True) + suffix
    if (run_dir / "prompt.md").read_text(encoding="utf-8") != expected_prompt:
        raise HandoffError("original prompt/packet/instruction snapshot changed; rebuild the packet")
    request, result = evidence["request"], evidence["result"]
    if not isinstance(request, dict) or not isinstance(result, dict):
        raise HandoffError("request and result must be JSON objects")
    if (request.get("type") != "tool_use" or request.get("name") != "Agent"
            or not isinstance(request.get("id"), str) or not request["id"]
            or result.get("type") != "tool_result" or result.get("tool_use_id") != request["id"]):
        raise HandoffError("evidence is not a linked native Agent request/result")
    submitted = request.get("input")
    if not isinstance(submitted, dict) or not isinstance(submitted.get("prompt"), str):
        raise HandoffError("Agent request has no prompt")
    if "wire_input" in evidence:
        if not isinstance(evidence["wire_input"], dict) or _digest(evidence["wire_input"]) != _digest(submitted):
            raise HandoffError("native wire input differs from the recorded Agent arguments")
        submitted = evidence["wire_input"]
    if submitted.get("resume") or submitted.get("run_in_background"):
        raise HandoffError("use a fresh, synchronous Agent turn")
    if (packet["kind"] in ("research", "research_verify")
            and submitted.get("subagent_type") != RESEARCH_AGENT):
        raise HandoffError("research requires the web-only software-localizer:usage-research agent")
    prompt = submitted["prompt"]
    if not prompt.startswith(prefix) or not prompt.endswith(suffix):
        raise HandoffError("submitted instructions differ from the complete original instructions")
    payload = prompt[len(prefix):len(prompt) - len(suffix) if suffix else None]
    if _digest(_json(payload)) != _digest(packet):
        raise HandoffError("submitted packet differs after JSON decoding (input-fidelity failure)")
    output = _read_json(output_path)
    if _digest(_result_json(result)) != _digest(output):
        raise HandoffError("saved output differs from the linked Agent result")
    bindings = {"packet_sha256": _digest(packet), "instructions_sha256": _digest(parts),
            "request_sha256": _digest(request), "result_sha256": _digest(result),
            "output_sha256": _digest(output), "tool_use_id": request["id"],
            "wire_input_sha256": _digest(evidence.get("wire_input")),
            "session_id": evidence["session_id"]}
    return bindings, packet, output


def receipt_path(output_path):
    return Path(str(output_path) + ".receipt.json")


def capture(run_dir, output_path, transcript, tool_use_id):
    """Retain each attempt, including refusals; never relabel an old attempt."""
    run_dir, output_path = Path(run_dir).resolve(), Path(output_path).resolve()
    attempt = run_dir / "handoffs" / uuid.uuid4().hex
    attempt.mkdir(parents=True)
    receipt = {"schema": 1, "status": "rejected", "evidence": str(attempt / "evidence.json"),
               "transcript": str(Path(transcript).resolve()), "tool_use_id": tool_use_id}
    try:
        for name in ("packet.json", "instructions.json", "prompt.md", "request.json"):
            lz_common.atomic_write_bytes(attempt / name, (run_dir / name).read_bytes())
        lz_common.atomic_write_bytes(attempt / "output.json", output_path.read_bytes())
        evidence = _select_records(transcript, tool_use_id)
        lz_common.atomic_write_json(attempt / "evidence.json", evidence)
        receipt.update(_check(run_dir, output_path, evidence)[0])
        receipt["status"] = "verified"
    except (HandoffError, OSError, UnicodeError, KeyError, TypeError, ValueError) as exc:
        receipt["error"] = str(exc)
    lz_common.atomic_write_json(attempt / "receipt.json", receipt)
    lz_common.atomic_write_json(receipt_path(output_path), receipt)
    if receipt["status"] != "verified":
        lz_common.fail(f"handoff refused: {receipt['error']}", lz_common.EXIT_CANNOT,
                       receipt=str(attempt / "receipt.json"))
    return {"ok": True, "receipt": str(attempt / "receipt.json"), **receipt}


def verify(run_dir, output_path):
    try:
        receipt = _read_json(receipt_path(output_path))
        if (not isinstance(receipt, dict) or type(receipt.get("schema")) is not int
                or receipt.get("schema") != 1 or receipt.get("status") != "verified"):
            raise HandoffError("receipt is missing or records a failed attempt")
        evidence_path = Path(receipt["evidence"]).resolve()
        evidence_path.relative_to((Path(run_dir) / "handoffs").resolve())
        checked, packet, output = _check(Path(run_dir), output_path, _read_json(evidence_path))
        if any(receipt.get(key) != value for key, value in checked.items()):
            raise HandoffError("receipt binding changed")
        return packet, output
    except (HandoffError, OSError, UnicodeError, KeyError, TypeError, ValueError) as exc:
        lz_common.fail(f"handoff receipt required: {exc}; capture the actual native Agent turn",
                       lz_common.EXIT_CANNOT)


def main():
    parser = lz_common.make_parser("packet_handoff.py", "Capture an actual native Claude Agent handoff.")
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--tool-use-id", required=True)
    args = parser.parse_args()
    result = capture(Path(args.run), Path(args.output), Path(args.transcript), args.tool_use_id)
    lz_common.emit(result)
    return lz_common.EXIT_OK


if __name__ == "__main__":
    lz_common.run_main(main)
