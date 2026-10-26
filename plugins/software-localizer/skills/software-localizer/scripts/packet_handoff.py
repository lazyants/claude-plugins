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
                        selected = {"result": block}
                        if "toolUseResult" in row:
                            if sum(isinstance(b, dict) and b.get("type") == "tool_result" for b in content) != 1:
                                raise HandoffError("ambiguous native completion metadata")
                            selected["completion"] = row["toolUseResult"]
                        results.append(selected)
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
    return {"session_id": session_ids.pop(), **requests[0], **results[0]}


def _text_content(content):
    if isinstance(content, list):
        if not content or any(not isinstance(b, dict) or b.get("type") != "text"
                              or not isinstance(b.get("text"), str) for b in content):
            raise HandoffError("unsupported Agent result content")
        content = "\n".join(b["text"] for b in content)
    if not isinstance(content, str):
        raise HandoffError("Agent result has no text")
    return content.strip()


def _valid_metadata(tail):
    metadata = (r"(?:agentId: [A-Za-z0-9_-]+(?: \((?:for resuming to continue this agent's work if needed"
                r"|use to resume this agent if needed"
                r"|use SendMessage with to: '[A-Za-z0-9_-]+', summary: '<5-10 word recap>' to continue this agent)"
                r"\))?(?:\r?\n|$))?"
                r"(?:<usage>\s*(?:total_tokens|subagent_tokens): [0-9]+\s+tool_uses: [0-9]+"
                r"\s+duration_ms: [0-9]+\s*</usage>)?")
    return not tail or re.fullmatch(metadata, tail) is not None


def _result_json(result):
    if result.get("is_error"):
        raise HandoffError("Agent returned an error")
    text = _text_content(result.get("content"))
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
    if not _valid_metadata(tail):
        raise HandoffError("unexpected text after Agent JSON answer")
    if not isinstance(answer, dict):
        raise HandoffError("Agent answer must be a JSON object")
    return answer


def _native_answer(evidence, submitted):
    result = evidence["result"]
    completion = evidence.get("completion")
    if completion is None:
        return _result_json(result)
    if (not isinstance(completion, dict) or completion.get("status") != "completed"
            or not isinstance(completion.get("prompt"), str) or result.get("is_error")):
        raise HandoffError("native Agent completion is missing or unfinished")
    prompts = [submitted["prompt"]]
    if isinstance(evidence.get("wire_input"), dict):
        prompts.append(evidence["wire_input"]["prompt"])
    if completion["prompt"] not in prompts:
        raise HandoffError("native completed prompt differs from the submitted input")
    if completion.get("agentType", submitted.get("subagent_type")) != submitted.get("subagent_type"):
        raise HandoffError("native completion names another agent type")
    if completion.get("handback") != "send":
        return _result_json(result)
    agent_id = completion.get("agentId")
    report = completion.get("handbackReport")
    if not isinstance(agent_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", agent_id) or not isinstance(report, dict):
        raise HandoffError("native handback has no linked agent/report")
    notice = (f'This agent\'s report was delivered to you as a message from "{agent_id}" '
              '(its SubagentHandback call). Read it there; it is not repeated here.')
    text = _text_content(result.get("content"))
    if not text.startswith(notice) or not _valid_metadata(text[len(notice):].strip()):
        raise HandoffError("handback notice differs from the completed Agent result")
    return _result_json({"content": report.get("text")})


def _check_prompt(prompt, prefix, suffix, packet):
    if not isinstance(prompt, str) or not prompt.startswith(prefix) or not prompt.endswith(suffix):
        raise HandoffError("submitted instructions differ from the complete original instructions")
    payload = prompt[len(prefix):len(prompt) - len(suffix) if suffix else None]
    if _digest(_json(payload)) != _digest(packet):
        raise HandoffError("submitted packet differs after JSON decoding (input-fidelity failure)")


def _assistant_text(message):
    content = message.get("content")
    if not isinstance(content, list) or any(not isinstance(b, dict) or b.get("type") not in
            ("text", "thinking", "redacted_thinking") for b in content):
        raise HandoffError("CLI final assistant message contains tools or unsupported content")
    return _text_content([b for b in content if b.get("type") == "text"])


def _jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                row = _json(line)
                if not isinstance(row, dict):
                    raise HandoffError("native JSONL rows must be objects")
                yield row


def _select_cli_records(transcript, cli_stream):
    rows = list(_jsonl(cli_stream))
    inits = [r for r in rows if r.get("type") == "system" and r.get("subtype") == "init"]
    completions = [r for r in rows if r.get("type") == "result"]
    responses = [r for r in rows if r.get("type") == "assistant" and any(
        isinstance(b, dict) and b.get("type") == "text" for b in (r.get("message") or {}).get("content", []))]
    if len(inits) != 1 or len(completions) != 1 or not responses:
        raise HandoffError("need one fresh CLI init, completed result and assistant answer")
    init, completion, response = inits[0], completions[0], responses[-1]
    native = list(_jsonl(transcript))
    requests = [r for r in native if r.get("type") == "user" and not r.get("isMeta")
                and not r.get("isSidechain") and isinstance((r.get("message") or {}).get("content"), str)]
    # CLI JSONL can store the initial user text as text blocks instead of a string.
    requests += [r for r in native if r.get("type") == "user" and not r.get("isMeta")
                 and not r.get("isSidechain") and isinstance((r.get("message") or {}).get("content"), list)
                 and r["message"]["content"] and all(isinstance(b, dict) and b.get("type") == "text"
                                                    for b in r["message"]["content"])]
    matched = [r for r in native if r.get("type") == "assistant" and not r.get("isSidechain")
               and (r.get("message") or {}).get("id") == response["message"].get("id")
               and any(isinstance(b, dict) and b.get("type") == "text"
                       for b in (r.get("message") or {}).get("content", []))]
    matched = list({_digest(r["message"]): r for r in matched}.values())
    if len(requests) != 1 or len(matched) != 1:
        raise HandoffError("CLI needs one original user request and one linked native assistant answer")
    tools = [b for r in native if r.get("type") == "assistant" for b in (r.get("message") or {}).get("content", [])
             if isinstance(b, dict) and b.get("type") == "tool_use"]
    return {"transport": "claude-cli", "session_id": init.get("session_id"), "init": init,
            "request": requests[0], "result": matched[0], "stream_response": response, "completion": completion,
            "tool_uses": tools}


def _check_cli(evidence, packet, prefix, suffix, output):
    init, request, result, completion = (evidence[k] for k in ("init", "request", "result", "completion"))
    session = evidence["session_id"]
    if (not isinstance(session, str) or not session or init.get("session_id") != session
            or completion.get("session_id") != session or request.get("sessionId") != session
            or result.get("sessionId") != session or evidence["stream_response"].get("session_id") != session):
        raise HandoffError("CLI records belong to different sessions")
    if (not isinstance(init.get("tools"), list) or sorted(init["tools"]) != ["WebFetch", "WebSearch"]):
        raise HandoffError("CLI packet child must have exactly WebSearch and WebFetch")
    if any(not isinstance(b, dict) or b.get("name") not in ("WebFetch", "WebSearch")
           for b in evidence["tool_uses"]):
        raise HandoffError("CLI child used a tool outside its web-only contract")
    if completion.get("subtype") != "success" or completion.get("is_error") is not False:
        raise HandoffError("CLI child did not finish successfully")
    if (request.get("type") != "user" or request.get("isMeta") or request.get("isSidechain")
            or request.get("message", {}).get("role") != "user"
            or result.get("type") != "assistant" or result.get("message", {}).get("role") != "assistant"):
        raise HandoffError("invalid native CLI request/answer records")
    # Preserve boundary newlines in the request; _text_content is only for answers.
    content = request["message"]["content"]
    prompt = content if isinstance(content, str) else "\n".join(b["text"] for b in content)
    _check_prompt(prompt, prefix, suffix, packet)
    native_answer = _result_json({"content": _assistant_text(result["message"])})
    stream_answer = _result_json({"content": _assistant_text(evidence["stream_response"]["message"])})
    final_answer = _result_json({"content": completion.get("result")})
    if len({_digest(o) for o in (native_answer, stream_answer, final_answer, output)}) != 1:
        raise HandoffError("CLI saved output, native assistant and completed result differ")
    return {"transport": "claude-cli", "request_sha256": _digest(request), "result_sha256": _digest(result),
            "init_sha256": _digest(init), "completion_sha256": _digest(completion),
            "stream_response_sha256": _digest(evidence["stream_response"]),
            "tool_uses_sha256": _digest(evidence["tool_uses"]), "session_id": session}


def _check(run_dir, output_path, evidence, output=None):
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
    if output is None:
        output = _read_json(output_path)
    common = {"packet_sha256": _digest(packet), "instructions_sha256": _digest(parts),
              "output_sha256": _digest(output)}
    if evidence.get("transport") == "claude-cli":
        return {**common, **_check_cli(evidence, packet, prefix, suffix, output)}, packet, output
    if evidence.get("transport") not in (None, "claude-agent"):
        raise HandoffError("unsupported handoff transport")
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
        wire = evidence["wire_input"]
        if (not isinstance(wire, dict)
                or _digest({k: v for k, v in wire.items() if k != "prompt"}) !=
                _digest({k: v for k, v in submitted.items() if k != "prompt"})):
            raise HandoffError("native wire input differs from the recorded Agent arguments")
        _check_prompt(wire.get("prompt"), prefix, suffix, packet)
    if submitted.get("resume") or submitted.get("run_in_background"):
        raise HandoffError("use a fresh, synchronous Agent turn")
    if (packet["kind"] in ("research", "research_verify")
            and submitted.get("subagent_type") != RESEARCH_AGENT):
        raise HandoffError("research requires the web-only software-localizer:usage-research agent")
    _check_prompt(submitted["prompt"], prefix, suffix, packet)
    if _digest(_native_answer(evidence, submitted)) != _digest(output):
        raise HandoffError("saved output differs from the linked Agent result")
    bindings = {"transport": "claude-agent", "packet_sha256": _digest(packet), "instructions_sha256": _digest(parts),
            "request_sha256": _digest(request), "result_sha256": _digest(result),
            "output_sha256": _digest(output), "tool_use_id": request["id"],
            "wire_input_sha256": _digest(evidence.get("wire_input")),
            "completion_sha256": _digest(evidence.get("completion")),
            "session_id": evidence["session_id"]}
    return bindings, packet, output


def receipt_path(output_path):
    return Path(str(output_path) + ".receipt.json")


def _write_new_output(path, answer):
    # Exclusive creation also refuses a sibling that won the race after the
    # earlier existence check. A partial failed write is retained as failed work.
    text = json.dumps(answer, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(text)


def capture(run_dir, output_path, transcript, tool_use_id=None, cli_stream=None, save_output=False):
    """Retain each attempt, including refusals; never relabel an old attempt."""
    run_dir, output_path = Path(run_dir).resolve(), Path(output_path).resolve()
    attempt = run_dir / "handoffs" / uuid.uuid4().hex
    attempt.mkdir(parents=True)
    receipt = {"schema": 1, "status": "rejected", "evidence": str(attempt / "evidence.json"),
               "transcript": str(Path(transcript).resolve()), "tool_use_id": tool_use_id}
    try:
        for name in ("packet.json", "instructions.json", "prompt.md", "request.json"):
            lz_common.atomic_write_bytes(attempt / name, (run_dir / name).read_bytes())
        if output_path.exists():
            lz_common.atomic_write_bytes(attempt / "output.json", output_path.read_bytes())
        if cli_stream is not None:
            lz_common.atomic_write_bytes(attempt / "cli-stream.jsonl", Path(cli_stream).read_bytes())
        evidence = _select_cli_records(transcript, cli_stream) if cli_stream is not None else _select_records(transcript, tool_use_id)
        lz_common.atomic_write_json(attempt / "evidence.json", evidence)
        if save_output:
            if cli_stream is None or output_path.exists():
                raise HandoffError("--save-output requires a CLI stream and a new output path")
            answer = _result_json({"content": evidence["completion"].get("result")})
            bindings, _, answer = _check(run_dir, output_path, evidence, output=answer)
            _write_new_output(output_path, answer)
            lz_common.atomic_write_bytes(attempt / "output.json", output_path.read_bytes())
        else:
            bindings = _check(run_dir, output_path, evidence)[0]
        receipt.update(bindings)
        receipt["status"] = "verified"
    except (HandoffError, OSError, UnicodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
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
    except (HandoffError, OSError, UnicodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
        lz_common.fail(f"handoff receipt required: {exc}; capture the actual native Claude turn",
                       lz_common.EXIT_CANNOT)


def main():
    parser = lz_common.make_parser("packet_handoff.py", "Capture an actual native Claude CLI or Agent handoff.")
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--transcript", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--tool-use-id")
    source.add_argument("--cli-stream")
    parser.add_argument("--save-output", action="store_true")
    args = parser.parse_args()
    result = capture(Path(args.run), Path(args.output), Path(args.transcript), args.tool_use_id,
                     cli_stream=Path(args.cli_stream) if args.cli_stream else None, save_output=args.save_output)
    lz_common.emit(result)
    return lz_common.EXIT_OK


if __name__ == "__main__":
    lz_common.run_main(main)
