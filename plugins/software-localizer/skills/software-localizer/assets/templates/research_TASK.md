# Research how the field names each canon term

A software project is being localized into the target language in the packet (`locale`). For
each item — a product term or UI label from the project's canon — find out how established
products and communities in the project's field (`field`; when it is null, infer it from the
`examples`) name the same concept in that language. A person who may not speak the language
will choose the canon's rendering with your evidence. You return data only: do not create,
edit or delete any file.

The packet's `style` is the project's tone for that language; use it only to judge how well a
rendering fits, not as evidence.

Use web search and page fetch. Check the operator's reference sites (`sites`) first when the
packet lists any, then other major products, help centres, documentation, standards, glossaries
and community sites for the field in that language.

For each item:

- Read `source`, `note` and `examples` to pin down what the term means in this product. A word
  can have several senses; research the one the product uses.
- `proposed` is the canon's current proposal (or null) and `current` lists renderings the
  project's existing translation uses. Treat both as candidates to test, not as answers.
- Record each source that renders the concept as a finding: `site` (who publishes it), `url`
  (the page you fetched), `rendering` (the term as that page writes it, with a short gloss if
  needed), `quote` (a short passage copied verbatim from that page that contains the
  rendering). Cite only pages you fetched yourself. A search-result snippet is not a fetched
  page. When a source refuses the fetch (bot protection, HTTP 403) or you cannot find the page,
  do not cite it; name it in the note instead.
- Give one `recommendation` (the rendering the evidence supports for this product), a
  `confidence` (`high`: several independent sources agree; `medium`: some support, or sources
  disagree; `low`: little or no outside evidence) and a `note`: why, what the proposal and
  current renderings get right or wrong, any other sense of the word the rendering could be
  confused with, and what you could not check.
- A concept that exists only in this product may have no outside evidence. Then give an empty
  `findings` list, `confidence: "low"`, and say so in the note; recommend the plainest rendering
  that fits the examples. A completed bounded search has `progress.status: "complete"` and an
  empty `remaining` list, even when it found no sources.
- If a search or fetch budget runs out, obey the tool's instruction to stop using it for this
  turn. The cap is shared by the coordinator and its nested agents; another subagent does not
  provide a new allowance. Keep the confidence your actual evidence supports and describe the
  limitation in the note. For each affected item set `progress.status: "resource_limited"` and
  list the exact unperformed queries, sites or checks in `remaining`. Do this for untouched
  items too; do not label unperformed work a source gap. Completed siblings stay `complete`.
- When an item has `resume`, read `resume.previous` and perform only its `progress.remaining`
  work. Retain its findings; do not claim to have fetched them again. Your new recommendation,
  confidence and note assess the combined evidence. Include the retained findings alongside
  any new findings, without changing their quotes. The coordinator retains each raw answer.

Answer every item in the packet, keyed by its `id`. Reply with ONE JSON object and nothing else:

```json
{"terms": {"<id>": {
  "recommendation": "…",
  "confidence": "high|medium|low",
  "note": "…",
  "progress": {"status": "complete|resource_limited", "remaining": []},
  "findings": [{"site": "…", "url": "https://…", "rendering": "…", "quote": "…"}]
}}}
```

## Packet

{{PACKET_JSON}}
