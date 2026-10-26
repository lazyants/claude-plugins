# Check the usage research for each canon term

Another turn researched how established products and communities in the project's field
(`field`) name each canon term in the target language (`locale`). Your job is to check that
research independently: re-open every source it cites and try to refute each recommendation.
A person will choose the canon's rendering from the evidence that survives your check. You
return data only: do not create, edit or delete any file.

The packet's `style` is the project's tone for that language; use it only to judge how well a
rendering fits, not as evidence.

Use page fetch (and web search where it helps). For each item:

- For every entry in `research.findings` (numbered from 0 in list order), fetch its `url`.
  Set `confirmed: true` only when the page you fetched contains the `quote` (differences in
  whitespace, quotation marks or dashes are fine) and the quote supports the stated
  `rendering`. Otherwise set `confirmed: false` and say why in `detail` (page did not load, bot
  protection, quote not on the page, quote says something else). A finding you could not check
  is not confirmed. A failed fetch that you actually attempted is a completed check, distinct
  from a check not performed because the turn's tool allowance ran out.
- When an item has `resume`, retain `resume.previous.findings` unchanged and check only the
  remaining indexes and counter-evidence work in `resume.previous.progress.remaining`. Return
  only the newly checked indexes; acceptance keeps the earlier checks. Otherwise answer every
  finding, each index exactly once.
- Then try to refute `research.recommendation`: do the confirmed findings support it for the
  sense the product uses (`source`, `note`)? Does it collide with another established meaning
  in the field? Is there a better-established rendering? Search for counter-evidence where it
  is cheap to find. Unconfirmed findings do not count as evidence.
- Give a `verdict`: `holds` (the confirmed evidence supports the recommendation), `weak` (it may
  be right, but the confirmed evidence is thin or mixed) or `refuted` (the evidence points
  elsewhere). Write `problems` (what failed or is thin; empty when nothing) and `better` (a
  rendering you would recommend instead, or an empty string).
- Copy the item's `research_sha256` into your answer unchanged.
- Set `progress.status: "complete"` and `remaining: []` after the bounded checks finish. If a
  search or fetch budget runs out, obey its instruction to stop using that tool in this turn;
  the cap is shared with the coordinator and its nested agents. Set `resource_limited` and list
  exact remaining source indexes and counter-evidence checks. Return only indexes actually
  checked (possibly none); never turn an unperformed check into `confirmed: false`. Keep an
  honest provisional verdict and problems; this checkpoint will not count as verified.

Answer every item in the packet, keyed by its `id`. Reply with ONE JSON object and nothing else:

```json
{"verdicts": {"<id>": {
  "research_sha256": "<copied from the item>",
  "progress": {"status": "complete|resource_limited", "remaining": []},
  "findings": [{"index": 0, "confirmed": true, "detail": "…"}],
  "verdict": "holds|weak|refuted",
  "problems": "…",
  "better": "…"
}}}
```

## Packet

{{PACKET_JSON}}
