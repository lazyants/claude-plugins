#!/usr/bin/env python3
"""Read-only accounting for the lean-worker investigation (#962).

Consume explicitly named JSONL records, never discover operator profiles or
launch workers. Supported formats: Claude session transcripts, Codex session
rollouts, and `codex exec --json` streams. Print one JSON line; malformed or
unmeasured records are fatal (exit 2, stderr only). No transcript text is emitted.

Claude input includes ordinary + cache-creation + cache-read input. Repeated
assistant blocks belonging to one message id are one request. Codex session
token_count events carry cumulative totals: repeated notifications must not be
summed. Exec turn.completed usage is per TURN, potentially several requests.
These counters measure context consumption, not dollars or subscription limits.

The records do not attribute tokens to task text, tools, or inherited context.
Never turn prompt bytes into a token estimate or call a residual 'overhead'.
See references/lean-worker-investigation.md for provenance and the adoption gate.
Stdlib-only; independent of durable-root state and all workflow hash bundles.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys


class UsageError(Exception):
    pass


def count(obj, key):
    value = obj.get(key)
    if type(value) is not int or value < 0:
        raise UsageError(f"{key}: expected a non-negative integer")
    return value


def codex_counts(obj):
    if not isinstance(obj, dict):
        raise UsageError("usage: expected an object")
    result = {key: count(obj, key) for key in
              ("input_tokens", "cached_input_tokens", "output_tokens")}
    if result["cached_input_tokens"] > result["input_tokens"]:
        raise UsageError("cached_input_tokens exceeds input_tokens")
    return result


def claude_counts(obj):
    if not isinstance(obj, dict):
        raise UsageError("usage: expected an object")
    raw = {key: count(obj, key) for key in
           ("input_tokens", "cache_creation_input_tokens",
            "cache_read_input_tokens", "output_tokens")}
    return {
        "input_tokens": sum(raw[key] for key in
                            ("input_tokens", "cache_creation_input_tokens",
                             "cache_read_input_tokens")),
        "cached_input_tokens": raw["cache_read_input_tokens"],
        "cache_creation_input_tokens": raw["cache_creation_input_tokens"],
        "uncached_input_tokens": raw["input_tokens"],
        "output_tokens": raw["output_tokens"],
    }


def summarize(path, fmt):
    try:
        raw = path.read_bytes()
        rows = []
        for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise UsageError(f"line {line_number}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise UsageError(f"line {line_number}: expected an object")
            if not isinstance(row.get("type"), str) or not row["type"]:
                raise UsageError(f"line {line_number}: missing or invalid record type")
            rows.append(row)
    except (OSError, UnicodeError) as exc:
        raise UsageError(f"cannot read UTF-8 record: {exc}") from exc

    samples = []
    models = set()
    if fmt == "claude-session":
        messages = {}
        for row in rows:
            if row.get("type") != "assistant":
                continue
            message = row.get("message")
            if not isinstance(message, dict):
                raise UsageError("assistant message: expected an object")
            # Synthetic local messages (e.g. API-error renderings) are not
            # provider requests and must not become zero-input observations.
            if message.get("model") == "<synthetic>":
                continue
            if "usage" not in message:
                raise UsageError("assistant message has no usage")
            mid = message.get("id")
            if not isinstance(mid, str) or not mid:
                raise UsageError("assistant usage has no message id")
            usage = claude_counts(message["usage"])
            if mid in messages:
                previous = messages[mid]
                if any(previous[key] != usage[key] for key in usage
                       if key != "output_tokens"):
                    raise UsageError("conflicting input usage for one message id")
                # Streaming blocks can carry a later output count for the same
                # request. Keep its maximum; never charge input twice.
                usage["output_tokens"] = max(previous["output_tokens"], usage["output_tokens"])
            messages[mid] = usage
            if isinstance(message.get("model"), str):
                models.add(message["model"])
        samples = list(messages.values())
        unit = "provider_request"
    elif fmt == "codex-session":
        previous = {key: 0 for key in
                    ("input_tokens", "cached_input_tokens", "output_tokens")}
        for row in rows:
            payload = row.get("payload")
            if row.get("type") in ("event_msg", "turn_context"):
                if not isinstance(payload, dict):
                    raise UsageError(f"{row['type']} payload: expected an object")
            if row.get("type") == "event_msg":
                if not isinstance(payload.get("type"), str) or not payload["type"]:
                    raise UsageError("event_msg payload has no event type")
            if not isinstance(payload, dict):
                continue
            if row.get("type") == "turn_context" and isinstance(payload.get("model"), str):
                models.add(payload["model"])
            if row.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue
            if "info" not in payload:
                raise UsageError("token_count has no info field")
            info = payload.get("info")
            if info is None:  # rate-limit-only notification, no usage
                continue
            if not isinstance(info, dict):
                raise UsageError("token_count info: expected an object")
            current = codex_counts(info.get("total_token_usage"))
            if any(current[key] < previous[key] for key in current):
                raise UsageError("cumulative token counts decreased; split/reset record")
            if current == previous:
                continue
            delta = {key: current[key] - previous[key] for key in current}
            if delta["cached_input_tokens"] > delta["input_tokens"]:
                raise UsageError("cached-input delta exceeds input delta")
            samples.append(delta)
            previous = current
        # One observation may cover more than one API request if notifications
        # were dropped. Do not label these as an exact request count.
        unit = "cumulative_usage_delta"
    else:
        for row in rows:
            if row.get("type") == "turn.completed":
                samples.append(codex_counts(row.get("usage")))
        unit = "completed_turn"

    if not samples:
        raise UsageError(f"no measured usage in {fmt} record")
    keys = samples[0].keys()
    return {
        "path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
        "format": fmt, "models": sorted(models),
        "observation_unit": unit, "observations": len(samples),
        "first_observation": samples[0],
        "total_observed": {key: sum(row[key] for row in samples) for key in keys},
        "attribution": "unavailable",
        "scope": "observed_usage_only_not_proof_of_job_completion",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", required=True,
                        choices=("claude-session", "codex-session", "codex-exec"))
    parser.add_argument("--worker-class", required=True,
                        choices=("translate", "review", "fix", "citation-judge", "orchestration"))
    parser.add_argument("records", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        records = [summarize(path, args.format) for path in args.records]
        identities = [record["sha256"] for record in records]
        if len(set(identities)) != len(identities):
            raise UsageError("duplicate record bytes (including profile aliases)")
    except UsageError as exc:
        print(f"worker-usage: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"worker_class": args.worker_class,
                      "classification": "caller_supplied_not_inferred",
                      "records": records}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
