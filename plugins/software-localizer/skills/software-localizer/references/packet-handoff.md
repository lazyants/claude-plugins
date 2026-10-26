# Claude packet handoff

`packet.json` records the intended input. It does not prove what a child received.
For canon, research, research verification, audit and review, `packets.py accept`
requires a receipt from the actual native Claude CLI session or synchronous
`Agent` call. Both check the complete original instructions and decoded packet.

## Direct CLI handoff

Use this route when a host hook rewrites Agent prompts. The trusted parent pipes
the complete generated prompt directly to a fresh CLI child, with its normal
profile settings and enabled plugins. The packaged agent supplies only
`WebSearch` and `WebFetch`; no file or shell tools are added to the child. It
follows the supplied canon, research, verification, audit or review task.

```bash
claude --agent software-localizer:usage-research --print --verbose \
  --output-format stream-json --session-id <new-uuid> --max-turns <agreed-limit> \
  < <batch-dir>/prompt.md > <batch-dir>/claude-stream.jsonl
python3 S/packet_handoff.py --run <batch-dir> --output <new-answer.json> \
  --transcript <actual-child-session.jsonl> --cli-stream <batch-dir>/claude-stream.jsonl \
  --save-output
python3 S/packets.py accept --root R --run <batch-dir> --output <new-answer.json>
```

Use the active profile's normal launcher. An installed plugin provides the named
agent; during development `--plugin-dir <plugin-root>` loads that same package.
The actual session JSONL is in that profile's `projects/<encoded-directory>/`
under the supplied UUID. Keep the stream and session log; never reconstruct them.
Use a fresh child for verification and respect the research turn's agreed bounds.

The CLI receipt binds the actual original user-message record, native assistant
answer, stream assistant message id, successful completion and init tool roster
to one session. It requires exactly the two web tools and refuses recorded calls
to any other tool. Multiple original user messages, changed input, tool changes,
unfinished/limited completions or mismatched answers are refused. `--save-output`
serializes the actual returned JSON only after these checks, without another
model copying it, and refuses an existing answer path. Omitting that flag checks
an already saved answer. A CLI receipt is identified as `claude-cli`, separately
from the `claude-agent` receipt; it does not pretend a native Agent call occurred.

## Native Agent handoff

1. Build the batch. It contains `prompt.md`, `packet.json`, the complete original
   instruction prefix/suffix in `instructions.json`, and `request.json`: complete
   `Agent` arguments serialized as ASCII JSON. JSON escapes decode to the original
   Unicode values, including U+202F, U+00A0 and fragment boundary spaces.
2. The trusted parent submits the decoded `request.json` object as the native
   `Agent` arguments, without rewriting its prompt or adding instructions. Use a
   fresh, synchronous child. The research and verification requests select the
   packaged `software-localizer:usage-research` agent, whose only tools are
   `WebSearch` and `WebFetch`. Start a different child for verification. Do not add
   file or shell tools to solve transport problems.
3. Save the returned JSON object as the answer file. Obtain the actual call's
   `tool_use` id from the parent's native session JSONL, normally
   `<profile-config>/projects/<encoded-working-directory>/<session-id>.jsonl`. Use that
   real log, never a reconstructed request, copied prompt or worker echo. Then:

   ```bash
   python3 S/packet_handoff.py --run <batch dir> --output <answer.json> \
     --transcript <actual-parent-session.jsonl> --tool-use-id <toolu_ID>
   python3 S/packets.py accept --root R --run <batch dir> --output <answer.json>
   ```

The capture reads the actual assistant `tool_use` and corresponding user
`tool_result`, checks that they belong to one session, and binds the receipt to
their complete records, the packet, original instructions and saved answer. It
also retains `wireToolInputs` for that call when the native log provides it and
checks both prompts against the original instructions and decoded packet. Wire
and displayed prompts may use different equivalent JSON escaping; other argument
fields must agree. It
compares decoded packet values and the entire instruction prefix and suffix;
equivalent JSON escaping/formatting is allowed. String whitespace and codepoints
are never normalized. Missing, conflicting, resumed, asynchronous, error or
unsupported records are refused. Wait for the complete synchronous result and
the session log to flush before capture. This adapter supports native Claude
JSONL `Agent` records; other providers/wrappers need their own transport adapter,
not a hand-authored lookalike log.

The output must equal the JSON returned by that call after decoding. Native text
blocks, optional JSON fences and trailing Claude `agentId`/usage metadata are
supported. Metadata must match the complete native format; its prefix cannot
hide another answer or arbitrary prose. The receipt is written beside the answer as `<answer>.receipt.json`.
Current native `SubagentHandback` completions are supported through the same
result row's `toolUseResult.handbackReport`: the completed status, agent type,
agent id and matching handback notice are checked, and the entire native
completion is bound into the receipt. Its completed prompt must equal the
checked submitted prompt (or its independently checked wire representation).
Appended native runtime notes are instruction changes and are refused even when
the completion attributes them to a harness. A counter is not an exception to
the original instruction contract. For example, a hook that appends a routing
block to every Agent prompt makes that Agent route fail; use the direct CLI
stdin route with the same normal settings, rather than disabling the hook or
grandfathering the changed input.
Acceptance replays the evidence checks and checks all receipt bindings before
writing any ledger, canon candidates or research. It consumes those verified
packet/output objects rather than rereading an unchecked answer.

## Failed attempts and scope

Each capture keeps an independent `handoffs/<attempt>/` with the original packet,
instructions, prompt, request, saved output, selected actual request/result
records when available, and a verified or rejected receipt. Later attempts never
rewrite that directory. Keep it with the run evidence. Rebuilding a batch does
not erase it. A corrected attempt does not turn the earlier failure into a pass.

An input-fidelity failure makes the whole affected call ineligible for an
exact-value audit/review verdict, even if its output schema and expected-value
hash pass. Preserve its raw answer and rejected receipt, then rerun just that
batch with the original values and instructions. Other batches need no rerun.
Do not accept the failed answer by attaching it to another call: the actual
returned JSON must match. Older runs without instruction snapshots/receipts need
a fresh build and turn before acceptance; existing historical evidence does not
gain verified transport by upgrading the plugin. Previously accepted research
with a known transport failure may remain as explicitly qualified lexical
evidence; it cannot establish exact UI typography. Previously discovered bad
audit/review input requires a new verdict for the affected values before they
are used as exact-value evidence.

The trusted parent and its local session log are the root of this check. This is
not an attestation against a parent that forges logs or edits all evidence. It
verifies the submitted native Agent prompt, not hidden system messages or a
provider's internal tokenization. It establishes input transport only: it does
not establish linguistic correctness, source truth, research completeness or
product improvement. The separate schema, value, research and project checks
still apply.

Codex translation still receives `prompt.md` directly on stdin; it produces an
unreviewed candidate, not a verdict. This native-Claude receipt adapter does not
claim to attest that Codex request. Its candidate requires the guarded Claude
review before export. Adapter coverage uses its own file-reading turn and
acceptance contract and is outside this packet protocol.
