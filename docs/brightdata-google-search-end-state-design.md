# BrightData Google Search Integration — End-State Design

> Status: approved design (not yet implemented) · 2026-09-15
> Grounded in: a 6-mode live latency/quality benchmark of the Bright Data SERP API and a full trace of the `web_search` pipeline (intake → planning → dispatch → merge → ranking → assembly).
> Companion audit: per-engine capability audit for DDGS, Brave, SearXNG, Tavily, Exa, and the personal Qdrant web index (see `docs/web-search-pipeline-multi-engine-plan.md`).

## 1. End state

After the rewrite, the system speaks to exactly one external SERP engine — **Google, via BrightData** — and treats it as a first-class engine in the retrieval pipeline rather than a bolted-on feed.

### The request contract

Every BrightData request is a single Google search call in full-JSON mode. There is one request shape across the whole engine — no "light" variant, no per-engine URL builders, no mode fork to maintain. The request is assembled from three sources that already exist: the search intent (which decides verbatim matching, mobile emulation, and whether the call is organic or news), the resolved locale and temporal window (which map to the engine's country, language, and time-filter tokens through the existing filter machinery), and the branch's page offset.

News searches hit Google's news endpoint, not the organic results page — the response shape differs and the intent demands it. Comparison and general queries search verbatim; conversational intents (news, coding, humanities, social) search without verbatim matching, because paraphrase-tolerant retrieval is what those intents need and verbatim mode correlates with the engine's query-truncation failure class. Social-media queries run under mobile emulation, because the mobile SERP is a materially different result set for those platforms. Two things are deliberately never sent: BrightData's own query optimizer (this system owns query generation, and enabling theirs would destroy the truncation-detection signal the integration depends on) and the AI Overview trigger (it does not fit the interactive latency budget — see Rejected Alternatives).

One knob is enabled on every request that the current integration doesn't use at all: `return_mismatch`. When Google returns results for a truncated query, BrightData by default discards the response and doesn't bill for it. With this knob on, the response arrives and the pipeline validates it itself — using the engine's own detected-query and spelling fields to tell a legitimate spelling correction apart from a truncation. The result is that a query which Google silently rewrote becomes a visible signal in the pipeline instead of a silently missing branch.

### What one response yields

A single 1-credit call returns, in full-JSON mode: the organic result list (with Google's own rank position, source name, display URL, highlighted match words, and last-modified date where present), Google's featured snippet, the people-also-ask block, related searches and refinement chips, the knowledge panel, forum answers, top stories, video and shopping rows where they appear, pagination, and the query-integrity metadata (detected query, spelling, result count, emptiness flag, country).

The parser converts this into two kinds of pipeline citizens rather than one flat list:

- **Organic rows** carry the URL, title, snippet, engine rank, source name, a classified source kind (forum / video / social / official / news), and the engine-provided date.
- **Answer-tier rows** — the featured snippet, each people-also-ask answer, and each knowledge-panel fact — enter the same candidate pool as first-class results, tagged with their tier and their source URL. They are not a parallel structure the downstream stages have to be taught to read; they are results, with a marker that says "this is an answer, weigh it accordingly."

The link problem is solved at the source: Google returns redirect-form links on this account, so the parser derives the true domain from Google's display URL (which always leads with the real host) and keeps the redirect as the fetch target — the fetch tool follows redirects natively. This is not cosmetic: the blocklist currently bypasses any result whose host it cannot parse, so redirect links silently escape domain-level blocking today.

### How the pipeline consumes it

**Ranking.** The engine's own rank position replaces the list-position proxy in the weighted reciprocal-rank fusion. Today the pipeline assumes a provider's returned list order *is* its quality order, which over-weights top-of-list positions from noisier providers and makes a Google #1 indistinguishable from a Google #9. With the engine rank flowing through, the fusion math weights a genuinely top-ranked result above a mid-list one from any provider — the pipeline's positional prior becomes engine-informed rather than provider-inferred.

**Answer tiers.** Answer-tier rows participate in normal ranking but carry a demotion multiplier at the evidence stage, so a featured snippet surfaces when it directly answers the question without crowding out the ten organic results beneath it. Ads and product-listing rows, where the intent calls for them (comparison), are treated the same way: present, tier-tagged, demoted.

**Candidate text.** The text fed to BM25, the bi-encoder, and the cross-encoder gains two signals it has never had: the publisher/source name (which tells a reranker that "Reddit · r/Chattanooga" is community discussion, not documentation) and Google's own highlighted match words (which are the engine's lexical-relevance markers for that snippet). This is Google's own relevance judgement, already paid for, entering the scoring inputs for the first time.

**Freshness.** Engine-provided modification dates populate the result's published-date field, which moves BrightData from the "relative-only" tier of the temporal-mode table to "native partial" — absolute date filters stop degrading this provider, and undated-result handling operates on engine dates instead of URL heuristics.

**Query expansion.** People-also-ask questions, related searches, and refinement chips flow into the graph-expansion seed pool under its existing bounds (max four seeds, support-gated, stable dedup). Today those seeds come from a stale local graph artifact; Google's own related-query suggestions are fresh, intent-matched, and arrive free with the same credit.

**Diversification.** Source-kind classification lets merge enforce per-kind diversity — without it, ten Reddit and YouTube rows can dominate a single branch's contribution to the fused ranking.

**Observability.** The engine rank, the query-integrity metadata (result count, detected query, request ID), and answer-tier counts land in the analytics tables that already have columns reserved for per-provider rank and payload detail. Typed failures — per-query rate bans, challenge pages, auto-throttling — surface as branch-level warnings with their documented codes rather than collapsing into a generic "invalid JSON" error, which is what happens today for every one of those conditions.

### What the six intents get

| Intent | Request shape | What the parser surfaces | What the pipeline does with it |
|---|---|---|---|
| `general` | Full JSON, verbatim matching, locale + time window | Organic + featured snippets + PAA + related | Engine rank in fusion; snippet/PAA rows as answer tier; expansion seeds |
| `ai_coding_and_infrastructure` | Full JSON, no verbatim matching | Organic + forum answers (top-answer flagged) + PAA | Forum answers as answer-tier rows; source-kind caps on forum/social |
| `digital_humanities` | Full JSON, no verbatim matching | Organic + knowledge panel + perspectives | Knowledge facts as entity grounding; perspectives as opinion signal |
| `comparison` | Full JSON, verbatim matching | Organic + shopping/PLA rows + PAA | Market rows tier-tagged and demoted; expansion seeds |
| `social_media` | Full JSON, **mobile emulation**, no verbatim matching | Organic (social/video source kinds) + video blocks | Mobile-first result set; diversification caps |
| `news` | **Google News endpoint**, time window | News rows (source, date, description) + top stories | Engine dates drive temporal filtering; rank in fusion |

### What leaves the system

Bing, Yandex, their URL builders, their response parsers, their region maps, and the Bing-envelope compatibility path — all of it. Two catalog aliases collapse into one Google provider. The light-JSON mode fork goes with them. What remains is one request path, one parser, one error mapper, one test surface.

### Benchmark numbers this design rests on

Google-only, same three queries, two repetitions, six response modes. Full JSON (`brd_json=1`): 2.52 s median latency, ~9 KB, organic fields `global_rank`/`rank`/`source`/`display_link`/`snippet_highlighted_words`/`icon`, sections `ai_overview`/`people_also_ask`/`related`/`pagination`/`forums`/`videos`. `parsed_light`: 3.76 s median, ~3 KB, only `global_rank`, sections `navigation`/`videos`. `brd_ai_overview=2`: 19.5 s median, 48.6 s max, three of six calls failed (`expect_element`, `no_ready_cookies`). Observed error surface: `failed_query_rejected` (HTTP 429 with a plain-text body), `502 captcha`, `no_ready_cookies`, `expect_element`. All 30 observed Google organic links were `goto?url=` redirect forms; `display_link` was the only reliable domain source.

## 2. Rationale, per decision

**Google-only.** Bing and Yandex never worked correctly in this integration — the Bing alias silently ran Google, the Yandex parser returned zero results, and their measured latency (11–36 s) exceeded the retrieval budget, so they contributed nothing the pipeline could use. Keeping them meant maintaining three code paths to get one engine's results. Removing them halves the adapter surface and eliminates a class of silent misconfiguration the alias routing had already produced once.

**Full JSON as the only mode.** Measured head-to-head on the same queries: full JSON answered faster than light mode (2.52 s vs 3.76 s median) while returning six organic fields light never carries and six to eight SERP sections light never returns. Light mode's only advantage — payload size — is irrelevant at a 9 KB scale. Keeping it meant maintaining a second parser path to receive fewer signals per credit.

**Engine rank in fusion.** The pipeline's entire positional signal today is "where in the returned list did this land," which conflates the provider's ordering with its quality. Google hands over the actual position; discarding it and re-deriving rank from list order is strictly worse than using what was paid for. This is the single highest-leverage ranking-quality change in the design.

**Answer-tier rows in the candidate pool.** Google's featured snippets and PAA answers are answer-grade text with a source URL, already in the response, and today they are parsed away. Promoting them to results with a tier marker means the reranker and the agent see them through the normal evidence path instead of a special case — and the tier multiplier keeps them from displacing organic results when they're merely adjacent rather than responsive.

**Google-authored expansion seeds.** The current seed source is a local graph artifact that goes stale. PAA questions and related searches are Google's own judgement of what else someone asking this question wants — fresh, intent-matched, and arriving inside a call that was already being made.

**Engine dates for freshness.** Date filters currently degrade providers without native date support. BrightData has the dates; surfacing them upgrades the provider's temporal mode and makes the undated-result policy operate on evidence rather than inference.

**Source-kind classification.** SERP results mix documentation, community threads, video pages, and vendor pages. Without typing, the fusion treats them as interchangeable and a single branch can saturate with one kind. A classifier over the source name (which Google already formats as "Reddit ·", "YouTube ·", or the publisher) is cheap and enables per-kind diversity caps plus better reranker text.

**Typed error mapper.** Every failure BrightData returns today — per-query rate bans, challenge pages, throttling, query truncation — collapses into "response was not valid JSON," which means the pipeline cannot distinguish a retryable failure from a permanent one, cannot honour a rate-limit window, and cannot tell a truncated query from an empty result. The documented error set is small, stable, and observable (it was observed live during testing); mapping it converts silent empty branches into visible, actionable signals.

**Domain from display URL.** Google's redirect-form links mean the parsed link is not the destination. Deriving the domain from Google's display URL (always the real host) restores blocklist integrity — without it, any blocked domain reached through a redirect link escapes filtering entirely.

**Knobs internal-only.** Device emulation, verbatim matching, and the news endpoint are provider decisions keyed on intent, not agent-facing parameters. Exposing them would bloat the tool contract with knobs agents can't reason about; keeping them internal preserves the "one tool, unambiguous purpose" contract while still getting per-intent behaviour.

**AI Overview on the background path.** Measured: 19.5 s median, 48.6 s max, three of six calls failed. It cannot ride the 20 s interactive budget. On the existing background-task infrastructure it becomes a grounded-synthesis source with citations at ~1 credit, off the latency budget entirely.

## 3. Rejected alternatives

**Keep light JSON as a fast path for shallow queries.** Rejected on measurement: full JSON was faster (2.52 s vs 3.76 s median) while carrying six organic fields and six-plus SERP sections that light omits. Light mode's payload advantage (~3 KB vs ~9 KB) buys nothing at this scale, and its omissions are exactly the signals the redesign exists to harvest. Two modes also means two parsers for one engine.

**AI Overview inline on every search.** Rejected on measurement: median 19.5 s, max 48.6 s, with a 50% failure rate (`expect_element`, `no_ready_cookies` observed live). It is a browser-launch path with documented 30–150 s element waits. It does not fit the interactive budget and would have to steal budget from every other provider on every search.

**Keep Bing and Yandex as secondary engines.** Rejected on both evidence and economics: the Bing alias never actually called Bing (a routing bug the rewrite fixes by deletion), the Yandex parser returned zero results, and both exceeded the retrieval budget consistently. They consumed maintenance surface and catalog slots while contributing nothing measurable.

**Side-band payload instead of result fields.** Rejected structurally: a detached payload disconnected from the result model means every downstream stage (fusion, rerank, evidence, expansion) needs custom plumbing to read it, and the rank signal never reaches the fusion math where it matters. Threading the signals onto the result model lets every existing stage consume them with no new machinery — and the public wire projection already strips fields the agent doesn't need.

**Enable BrightData's query optimizer.** Rejected because this pipeline already owns query generation through its own rewrite stage. Stacking a second optimizer makes the effective query opaque (`general.query` would report BrightData's adjusted version), destroys the ability to detect Google-side truncation by comparing sent vs. detected queries, and duplicates work the rewriter already does.

**Full JSON + HTML mode as the default.** Rejected on payload and latency: 508–913 KB per call at 6.4 s median (vs 9 KB at 2.52 s) with zero additional parsed fields over full JSON. The HTML string is useful only as a raw fallback when the parser is broken — a recovery path, not a default.

**Expose the knobs to the agent.** Rejected on tool-design grounds: device emulation, precise geo, and safe-search are search-engine mechanics, not agent decisions. Making them parameters would grow the tool contract, invite parameter-guessing, and produce requests the provider layer would have to second-guess. They stay internal, keyed on intent.

**Precise geo (`uule`) now.** Deferred, not rejected: it requires extending the locale contract to carry a city or encoded location, which is a deliberate contract change rather than a knob flip. The design leaves the seam in place and the mapper unwired until that change is wanted.