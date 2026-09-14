# `content/resolvers/` audit — per-resolver evaluation vs equivalents

Date: 2026-09-13. Method: 4 read-only scouts briefed all 22 resolvers (mechanism,
upstream, caps, errors, weakness); compared against `MakiAi/wikipedia-to-markdown`
`app.py` (full source pulled via Hub), GitHub code search (MediaWiki clients,
`monty-python` PyPI `readme_renderer` handling, `pymupdf4llm` API, HN
Algolia/Firebase patterns), and web (MediaWiki Action vs REST API guidance, SE
filters, Wayback CDX).

## Headline: Tier-1 design is sound; gaps are per-resolver, not architectural

Ordering (`specialized_pipeline._resolve_tier1`: document → raw_text → DOI →
registries → Discourse/SE/GitHub → HN/Reddit → Twitter → YouTube → Wikipedia →
arXiv → Telegram; Wayback as Tier-2 fallback) is the right shape: deterministic
API resolvers before heuristic scraping. Every resolver follows parse→fetch→render
with a typed `*Error`, so failures fall through instead of poisoning.

## Deep dive: `wikipedia.py` vs `MakiAi/wikipedia-to-markdown`

Ours wins decisively — different leagues, not different tradeoffs:

| | Ours | MakiAi `scrape_wikipedia_to_markdown_final()` |
|---|---|---|
| Source | MediaWiki Action API `action=parse&prop=text&redirects=1&maxlag=5`, contactable UA (`WIKIPEDIA_USER_AGENT`), 503 retry honoring `Retry-After` | `requests.get(url)` page scrape, hardcoded Chrome UA |
| Content root | Full parse HTML → strip `sup.reference`/navboxes/nav divs → generic extractor | `div.mw-parser-output` (good instinct!) → `html2text` |
| Structure | Sections/tables preserved via extractor; disambiguation detected → 25-link option list; 12 non-article namespaces rejected; mobile→desktop normalize | **Drops everything after Japanese `"\n## 脚注"`** — silently truncates every non-Japanese article at the wrong place or not at all; `[編集]` regex only matches Japanese edit links |
| Robustness | Redirects followed, API errors typed as `WikipediaError` | Returns error *strings as content*; no namespace/redirect handling |

One idea worth stealing from theirs: **scope to `div.mw-parser-output` before
generic extraction**. Our cleanup strips chrome then hands the whole page to
`extract_html_as_markdown`; scoping to the parser-output root first (their one good
decision, also what `Karmin_Ae/web_extractor.py` does with
`article/main/[id*=content]` fallback) would remove infobox/TOC leakage at the DOM
layer instead of regexing it downstream. Longer-term, MediaWiki's REST
`/v1/page/{title}/html` + `/summary` endpoints are the documented fast path for
HTML/summary reads, but Action `parse` remains the most complete interface — keep
it, add `prop=sections` paging for huge articles (current one-shot `prop=text`
balloons on long pages).

## Registries (PyPI / npm / crates / HF) — good, same two bugs everywhere

All four hit the right endpoints (PyPI JSON, `registry.npmjs.org`,
`crates.io/api/v1`, Hub API + raw README) with clean meta→links→README shapes.
Common gaps: **no version pinning** (PyPI ignores `/<version>/`, crates fetches
`max_version` README only, npm uses `dist-tags.latest`), and sequential
meta→README fetches that could be parallel. Standout external comparison:
`monty-python` renders PyPI descriptions via
`readme_renderer.{markdown,rst,txt}` keyed on `description_content_type` —
correct, because PyPI descriptions are RST as often as Markdown. Ours passes
`description` raw into `sanitize_markdown`, so RST-heavy READMEs render as
underline-soup. **Fix: branch on `description_content_type`
(markdown→as-is, `text/x-rst`→`readme_renderer.rst`, plain→wrap), and honor
`/<project>/<version>/` via `GET /pypi/<p>/<v>/json`.** HF additionally
truncates `org/sub/proj` to 2 segments and ignores `/resolve/`+`/tree/` URLs —
accept 3-segment IDs and strip revision prefixes.

## arXiv / Unpaywall — best resolvers in the directory, two nits

arXiv (export API metadata + `%PDF-` magic-checked download +
`pymupdf4llm.to_markdown(pages=…)` → `get_text("markdown")` → text fallback)
matches the ecosystem consensus (`arxiv-corpus-builder`,
`research-extract-pdf-papers` all converge on `pymupdf4llm`). Nits: it opens the
PDF with `pymupdf` just to count pages, then re-iterates inside the helper — pass
the already-open `doc` through; and scanned PDFs go silent-empty instead of
setting a truncated/OCR flag. Unpaywall's shape (metadata artifact → OA PDF
append, 5 MiB cap, `quality_score` 0.8 fallback) is right; the baked-in
`academic_researcher@kindly.ai` contact email should be `UNPAYWALL_EMAIL` env,
and a Crossref fallback would cover Unpaywall 404s.

## GitHub family + Discourse — pagination is the whole story

- `github_repo`: GraphQL-with-token → REST fallback is right; but README is
  hardcoded to `HEAD:README.md` — try `readme.md`/`README.rst`/`.txt` variants
  before emitting `_No README found_`.
- `github_issues`: real cursor pagination, accepted-truncation banner — the model
  citizen. Only gap: token mandatory (no REST fallback) + labels/milestone dropped.
- `github_pulls`: **no pagination** (`comments(first: 50)`, REST issues-comments
  unpaged) and review threads (`Review`/`ReviewComment` collections) never
  fetched — PR feedback beyond issue-chatter is lost.
- `github_discussions`: top-level paginated, but **replies never follow
  `endCursor`** despite the query already fetching `pageInfo` — >50
  replies/comment silently lost next to a renderer that already supports the
  truncation banner.
- `discourse.py`: public `/t/{id}.json`, unauthenticated, `raw`-preferred with
  extractor fallback — correct minimal design; hard `replies[:20]` with no
  `?page=N`/`post_stream.stream` walk. Same one-line-class fix as
  pulls/discussions: follow the cursor you already request.

## Social/threads — HN and SE are exemplary, Twitter is a stub, Reddit overreaches slightly

- `hackernews.py`: Algolia `items/<id>` → Firebase fallback, no auth, depth-4
  nesting — matches the documented best practice (`simonw` Algolia `items` API,
  `junipr/hacker-news-scraper` Firebase+Algolia dual-source). Only gap: Firebase
  `kids` aren't recursed, so deep threads rely entirely on Algolia's nested
  payload. Minor.
- `stackexchange.py`: official 2.3 API, `backoff` honored, accepted-first
  ordering, `body_markdown`-preferred — exemplary. One real trap the scout
  confirmed: default `withbody` filter doesn't include `body_markdown`, so
  without `STACKEXCHANGE_FILTER` configured it silently downgrades to
  HTML→markdownify; pin a filter that includes `body_markdown`, and add
  `/comments` (currently question/answer comments dropped).
- `reddit.py`: 4-layer cascade (JSON → old.reddit → Arctic Shift → Apify) is
  genuinely resilient and the `APIFY_REDDIT_FIRST` escape hatch is good ops. But
  the renderer feeds Apify's flat comments through the nested-JSON path with
  `replies=None` — thread structure flattened. Preserve parent IDs or mark the
  output flat.
- `twitter.py`: single paid Apify actor, inert without token — honest
  fallthrough, but zero free fallback. A syndication/oEmbed stub
  (`cdn.syndication.twimg.com`, or even Tier-2 og:meta) would beat today's empty
  handoff.
- `telegram.py` (MTProto, thread limit 100) and `youtube.py` (yt-dlp +
  transcript cascade, `[:2000]` description) are thin-but-correct adapters;
  YouTube should emit a structured stub (title/thumbnail via oEmbed) instead of
  raising when both transcript and description are empty.

## Documents / raw_text / Wayback — small, sharp fixes

- `document.py`: correct dispatcher (PyMuPDF / MarkItDown with PK+OLE2 magic
  validation / native ipynb / columnar renderers), but PDF is
  `get_text("text")` — tables/columns collapse, scanned PDFs empty with no OCR
  signal. Route through the same `pymupdf4llm` path arXiv already uses, and
  surface `metadata.truncated=true` instead of only a trailing note past 30 pages.
- `raw_text.py`: over-broad (`.json/.csv/.svg/.rs` served as `text/markdown`) +
  forced UTF-8 (`errors="replace"` corrupts Latin-1/Shift_JIS) +
  `include_links=False` default hiding the typed link graph. Charset-sniff,
  narrow the extension set, default links on for typed formats.
- `wayback.py`: availability-API-only with total `None`-swallowing. Add CDX
  fallback (`/cdx/search/cdx?url=…&output=json&filter=statuscode:200&limit=1&sort=closest`)
  and direct `/web/<ts>/<url>` construction when `closest` is null, and
  distinguish "no archive" from "API down" in the return.

## Proposed order (ROI)

1. PyPI RST branching + version pinning; crates/npm version honoring (~30
   lines, fixes visible garbage).
2. GitHub pulls/discussions/Discourse pagination cursors (fields already
   fetched; follow them).
3. Wikipedia: scope to `mw-parser-output` + `prop=sections` paging; SE: pinned
   `body_markdown` filter + `/comments`.
4. Wayback CDX fallback + typed no-archive reason; document.py `pymupdf4llm`
   path + truncated flag; raw_text charset + links default.
5. Reddit Apify nesting, Twitter free stub, YouTube stub, HF 3-segment IDs,
   Unpaywall env email + Crossref — opportunistic, each isolated.
