# Composio Search Observed Findings

Observed during live Composio CLI testing on 2026-05-10.

This note captures practical caveats from the tested Composio Search toolkit tools. It intentionally uses the names exposed by Composio.

## Composio Similarlinks

Composio Similarlinks returned relevant links for the tested FastMCP middleware source URL, but the observed payload did not include snippets or page content.

Observed useful fields:

- `title`
- `url`
- `score`

Observed limitation:

- Results were useful for finding related pages, but not sufficient for judging page content without a follow-up fetch/read step.

## Composio Image Search

Composio Image Search returned useful image URLs and image metadata.

Observed useful fields:

- `title`
- `source`
- `link`
- `original`
- `thumbnail`

Observed limitation:

- Image URL accessibility is external to the returned metadata.
- Licensing/commercial reuse status is external to the returned metadata and must be verified from the result page or any available license details.

## Composio LLM Search

Composio LLM Search returned strong result records.

Observed useful fields:

- `answer`
- `results[].title`
- `results[].url`
- `results[].content`
- `results[].score`
- `images`

Observed limitation:

- The synthesized answer added an extra version claim that was not directly requested. The ranked result records were stronger evidence than the generated answer text.

## Composio Web Search

Composio Web Search returned useful citations.

Observed useful fields:

- `answer`
- `citations[].title`
- `citations[].url`
- `citations[].snippet`
- `citations[].publishedDate`

Observed limitation:

- It exposes fewer controls than Composio LLM Search. In the inspected schema, Composio Web Search accepted only `query`, while Composio LLM Search exposed result count, search depth, image inclusion, domain filters, answer inclusion, and raw-content inclusion.
