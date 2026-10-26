# Native Claude packet handoff

`packet.json` records the intended input. It does not prove what a child received.
For canon, research, research verification, audit and review, `packets.py accept`
requires a receipt from the actual synchronous native Claude `Agent` call.

## Dispatch and receipt

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
   `~/.claude/projects/<encoded-working-directory>/<session-id>.jsonl`. Use that
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
refuses disagreement between wire input and the recorded Agent arguments. It
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
