# 🔍 DeepSearch — keyless-first multi-engine web search + RRF fusion

A single-file Python CLI (stdlib + `requests`) that searches **14 engines in parallel** —
mostly **keyless** — canonicalizes, deduplicates, and merges their ranked lists with
**Reciprocal Rank Fusion** (Cormack et al., SIGIR 2009), giving you one quality-ranked,
consensus-scored result set. Built and live-tested September 2026, when most "free search
APIs" quietly died.

```
$ deepsearch.py "freqtrade strategy performance 2026" -E all -n 10

engines_ok: [arxiv bing brave ddg gnews marginalia openalex searxng serpapi wikipedia …]
top fused:
  rrf=0.064 ×4 engines [searxng,ddg,brave,serpapi]  GitHub - freqtrade/freqtrade-strategies
  rrf=0.049 ×3 [searxng,ddg,serpapi]                Strategy Quickstart - Freqtrade docs
```

## Why

Every 2025–2026 "deep research" agent leans on paid APIs (Tavily, SerpAPI, Exa). When their
free tiers cap or the endpoints bot-wall you, the agent hard-fails. DeepSearch is designed
**keyless-first with graceful degradation**: 8 general-web + 6 vertical engines run concurrently
per query; a dead engine costs nothing (circuit-skipped in the timing report), and the RRF merge
turns *engine agreement* into a computable confidence signal — an answer found by 4 independent
indices is more trustworthy than one search API's #1.

## Install

```bash
git clone https://github.com/GentelZole/deepsearch && cd deepsearch
pip install requests            # trafilatura optional, for the --read fallback chain
python3 bin/deepsearch.py --health   # should show most engines true
```

**Optional but recommended — self-hosted SearXNG** (the only reliable keyless general-web
metasearch left in 2026; all 22 public instances we tested disabled JSON or bot-walled):

```bash
docker run -d --name searxng -p 8899:8080 -v ./docs/searxng-settings.yml:/etc/searxng/settings.yml:ro \
  --env-file ./docs/searxng.env searxng/searxng:latest
```

(see `docs/ENGINES.md` for the two required settings: `formats: [html, json]` and `limiter: false`)

Optional keys (off by default): `export BRAVE_API_KEY=*** SERPAPI_KEY=*** (free tiers).
Optional local [Firecrawl](https://github.com/firecrawl/firecrawl) on `:3002` → best full-page markdown.

## Usage

| Command | What |
|---|---|
| `deepsearch.py "q" -E all` | Parallel fan-out, RRF fusion, consensus counts |
| `deepsearch.py "q" -E ddg -n 5` | One engine: `searxng ddg bing marginalia brave serpapi firecrawl wikipedia openalex arxiv hn gnews bnews stackexchange wayback` |
| `deepsearch.py "q" -E all -s 3` | Also run the top-3 fused URLs through the full-page reader |
| `deepsearch.py --read URL` | Full-page extraction: Firecrawl → trafilatura → raw |
| `deepsearch.py --health` | Live probe every engine (JSON map) |

`-o text` for human output; default JSON. Fused items carry `rrf`, `consensus`, `engines[]`.

## Engine roster & honest status (live-tested 2026-09-09, 58 candidates)

**Ship:** ddg (POST-only), bing (`/ck/a` base64-unwrapped), marginalia (`api.marginalia.nu/public`
— the api2 "public key" now 401s), searxng (self-hosted), wikipedia, arxiv, openalex, hn-algolia,
google/bing news RSS, stackexchange, wayback-CDX, brave, serpapi, firecrawl.

**Don't bother** (all verified dead/blocked from datacenter IPs): public SearXNG JSON (22/22),
DDG-lite, Mojeek captcha, Startpage proof-of-work, Ecosia 403, Yandex captcha, Lobsters JSON,
4get (DNS gone), Jina keyless (Cloudflare challenge), s.jina.ai (key now required), Google
`gb`/`gws` scraping (consent wall). Full evidence: `docs/ENGINES.md`.

## Design notes

- **RRF** `score(d) = Σ_engines 1/(60 + rank)` — parameter-free, needs no calibrated scores,
  beat Condorcet/CombMNZ in the paper. k=60 damps outlier top-ranks.
- **Dedup**: URL canonicalization (host normalize, strip trailing slash; DDG `uddg=` +
  Bing `u=a1<base64>` unwrapped *before* canon).
- **Politeness**: honest UA on keyless verticals, 6-way concurrency cap, 25s timeouts, RSS
  endpoints preferred over HTML scraping where offered. Respect each service's ToS — this is a
  low-volume research tool, not a scraper farm.
- **Failure mode**: engines 429 as traffic grows — RRF doesn't care who answered, so partial
  results still rank correctly. `--health` before batch runs.

## License

MIT · CLI + docs © 2026 Eng. Tarig Monfal Alnorbekhit · tareqmonaffal@gmail.com
