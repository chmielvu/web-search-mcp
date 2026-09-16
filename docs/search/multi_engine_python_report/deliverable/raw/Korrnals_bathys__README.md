<p align="center"><img src="docs/assets/banner.svg" alt="Bathys" width="720"></p>

# Bathys

**A single local deep-research search service for AI agents.** This is a standalone product, not a wrapper over someone else's services: Bathys implements the entire pipeline itself — metasearch with deduplication and resilience to blocking, two-tier extraction (an HTTP engine by default, a headless browser only for JS pages), five-stage query-tailored distillation with hard budgets, a TTL source cache, robots ethics, metrics and diagnostics. Metasearch and extraction are packaged as swappable internal engines (SearXNG, Crawl4AI) — they can be replaced, and the product remains Bathys. No cloud quotas; no LLM inside — synthesis stays with the calling agent, and distillation is deterministic (BM25).

The sonar finds the coordinates, the bathyscaphe dives for the full texts, and the distiller hoists on deck only what answers the question.

![python](https://img.shields.io/badge/python-3.10%2B-blue)
![version](https://img.shields.io/badge/version-0.14.1-9cf)
![mcp](https://img.shields.io/badge/MCP-stdio%20server-6f42c1)
![license](https://img.shields.io/badge/license-MIT-green)

[English](README.md) | [Russian](README.ru.md)

**Navigation:** [⚡ Quick start](#-quick-start) · [🧹 Removal](#-removal) · [🔌 Harness integration](#-harness-integration) · [🧠 Teach your agent](#-teach-your-agent-to-work-effectively) · [🧭 Use cases](#-common-use-cases) · [🛠 Tools](#-tools) · [📊 Token savings](#-token-savings) · [📚 Documentation](#-documentation) · [📍 Status](#-status)

## ⚡ Quick start

**Option 1 — install script** (recommended; Python ≥ 3.10):

```bash
curl -fsSL https://raw.githubusercontent.com/Korrnals/bathys/main/install.sh | bash
```

The script installs the package from PyPI into a private venv (`~/.local/share/bathys/venv`, no sudo), adds it to `PATH` and runs the full setup. Re-running it is a safe update.

**Option 2 — pip** (the same thing, done manually):

```bash
pip install bathys
bathys setup
```

What `bathys setup` does:

| Step | Action |
|---|---|
| 1 | installs the headless browser — needed only for JS pages (regular pages are read by the built-in HTTP engine) |
| 2 | registers the MCP server in every harness it finds (zcode, Claude, Cursor, the VS Code family and others — 14 in total, see [Harness integration](#-harness-integration)) |
| 3 | copies the researcher subagent into the found harness directories |
| 4 | prints the summary and hints (`bathys doctor` — self-diagnostics) |

SearXNG does not need to be installed separately — the backend starts automatically on the first search: an external instance is checked first, then docker/podman, then native mode (a clone in `BATHYS_SEARXNG_HOME`).

<details>
<summary><b>Alternative routes</b> — npm, sources, version pinning</summary>

**npm** (Node-first environments; the wrapper installs the Python package itself):

```bash
npm install -g bathys-mcp
bathys-mcp setup
```

**From sources** (development):

```bash
git clone https://github.com/Korrnals/bathys.git && cd bathys
python3.12 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/bathys setup
```

**Pinning a specific version** — an install-script variable:

```bash
BATHYS_INSTALL_VERSION=0.7.0 bash install.sh
```

A minimal image without `ensurepip`: the script and `setup` bootstrap pip themselves via `get-pip.py` — details in [docs/getting-started/install.md](docs/getting-started/install.md).

</details>

### 🧹 Removal

```bash
bathys uninstall               # detach Bathys from all harnesses
bathys uninstall hermes zcode   # pointwise, only the named ones
bathys uninstall --purge        # + delete the venv, cache and data
```

`uninstall` removes **only the `bathys` entries** from harness configs (a `*.bathys-backup-*` backup is created before any change; foreign servers and subagents are not touched). `--purge` additionally deletes the `~/.local/share/bathys` and `~/.cache/bathys` directories; remove the `bathys/venv/bin` line from `.profile`/`.bashrc` manually. Details and backup restore — in the [runbook](docs/operations/runbook.md).

## 🔌 Harness integration

**Automatically — the whole stack:** `bathys setup` (see above) registers the server in every harness it finds.

**Pointwise — when you need it exactly here:**

```bash
bathys install                # auto-detect all installed harnesses
bathys install hermes         # Hermes only (a missing config will be created)
bathys install --list         # all supported targets with paths
bathys install --print-config # ready-made blocks for manual pasting
```

Detected: zcode, Claude Code, Claude Desktop, Cursor, the VS Code family (Cline / Roo Code / Kilo Code), Gemini CLI, Windsurf, Zed, opencode, goose, Hermes; each has its own format (JSON schemas and YAML outlines for goose/hermes), and writes are idempotent with a backup. For Pi (badlogic pi-mono), which has no MCP config, there is a drop-in into `AGENTS.md`. Custom integrations live in the [integrations/](integrations/) directory.

<details>
<summary><b>Manual wiring</b> (when you edit the configs yourself)</summary>

Bathys is a stdio MCP server: the `mcpServers` block is the same everywhere, and only the file it goes into depends on the harness. `command` is an absolute path to the binary (`~` is not expanded inside JSON); `BATHYS_SEARXNG_HOME` is optional. Ready-made blocks for every client: `bathys install --print-config`.

```json
{
  "mcpServers": {
    "bathys": {
      "command": "/path/to/bathys",
      "env": { "BATHYS_SEARXNG_HOME": "/path/to/searxng-home" }
    }
  }
}
```

| Harness | Guide |
|---|---|
| zcode | [docs/integrations/zcode.md](docs/integrations/zcode.md) |
| Claude Code / Claude Desktop | [docs/integrations/claude-code.md](docs/integrations/claude-code.md) |
| Cursor | [docs/integrations/cursor.md](docs/integrations/cursor.md) |
| Any other MCP client | [docs/integrations/generic-mcp.md](docs/integrations/generic-mcp.md) |

</details>

## 🧠 Teach your agent to work effectively

Configuration is only half the job. Out of the box the harness receives an **instructions-playbook** (a tool-choice matrix), **annotations** and **three strategy prompts** — `bathys_deep_research`, `bathys_source_audit`, `bathys_fresh_scan` — so it picks Bathys tools natively. Stronger still is the profile: the [`agents/bathys-researcher.md`](agents/bathys-researcher.md) subagent with three skills, to which deep research is delegated wholesale; for clients that do not surface MCP instructions, there is the [`agents/HARNESS-DROPIN.md`](agents/HARNESS-DROPIN.md) drop-in for `AGENTS.md` / `CLAUDE.md` / `.cursor/rules`.

The step-by-step path "out of the box → subagent → drop-in" and the footer-signal table — in ["Live cases", section C](docs/getting-started/cases.md).

## 🧭 Common use cases

**Technology comparison.** Asked "which one to pick for heavy load in 2026?", the agent makes a single `deep_research`, refines the query with terms from what it found, and verifies the conclusion against two sources: one call instead of a "search + N reads" chain, and 7.5k characters reach the context instead of ~35k.

```text
[bathys: 34 raw hits, top 8 considered · dove 3 pages · 35669 ch fetched → 7508 ch returned · 1.3s]
```

**Auditing a contested claim.** "Is it true that the benchmarks for X dropped?" — the agent takes the `bathys_source_audit` strategy: reads the links from the discussion in batch, searches for rebuttals, and delivers a verdict with a URL for each thesis. A dead link costs one line, not a broken call.

**A fresh snapshot.** "What's new in Y in the last two weeks?" — the `bathys_fresh_scan` strategy: a search with `time_range=week`, batch reading, a dated summary; a stale `cache HIT` is cured by a single `refresh=true`.

A full walkthrough of all the cases — user-facing, autonomous agents and operations — with live dialogues: **[docs/getting-started/cases.md](docs/getting-started/cases.md)**.

## 🛠 Tools

| Tool | What it does |
|---|---|
| `deep_research(query, max_sources=3, …)` | searches, reads the top sources in parallel, returns a merged query-tailored distillate. The first call for any research question. |
| `web_search(query, max_results=8, …)` | a ranked list of links with snippets, without page content; `as_json=true` — clean JSON for programs. |
| `read_url(url, query=None, find=None)` | reads a page (including text PDFs); with `query` — relevant passages; with `find` — exact search over the source cache without the network. |
| `library_docs(library, query)` | up-to-date library documentation from the primary source, distilled to the question; repeat calls are free (cache). |
| `read_urls(urls, query=None, total_chars=12000)` | batch-reads up to 10 known pages; the budget is split across the successes, and a failed page costs one line, not a broken call. |
| `source_check(claim, urls?)` | deterministic claim verification without an LLM: sources → parallel dives → verdict `SUPPORTED/CONTRADICTED/UNCLEAR/MISSING-EVIDENCE` with confidence; source classes, a domain-independence cap. |

Search is narrowed by the shared `time_range`, `category`, `engines`, `language` filters. The live footer of a response shows compression and cache: `[bathys: 41 raw hits, top 3 considered · dove 3 pages · 35669 ch fetched → 7508 ch returned · 3.2s]`.

## 📊 Token savings

The scale is honest and character-based; tokens ≈ `chars/4`; every figure is taken from the footer of a real call.

| Call | From the network | To the agent | Compression |
|---|---|---|---|
| `web_search` | 37,549 chars | 1,986 chars | 18.9× |
| `read_url` | 17,063 chars | 2,325 chars | 7.3× |
| `deep_research` (3 pages) | 35,669 chars | 7,508 chars | 4.7× |

- **Two-tier extraction** — regular pages are read by the built-in HTTP engine (milliseconds, no browser) and JS shells by headless Chromium; then query-tailored distillation with hard character budgets.
- **Source cache before distillation** — SQLite stores the raw text, so re-reading a page from a different angle is free and requires no network.
- **Zero cloud quotas** — `deep_research` replaces a "search + N reads" chain — that is, N+1 quota charges — with a single local call.

The methodology and thresholds — in [docs/operations/metrics.md](docs/operations/metrics.md).

## 📚 Documentation

| Section | What's inside | Who it's for |
|---|---|---|
| [docs/index.md](docs/index.md) | The hub: the documentation tree and three reading routes | everyone — the entry point |
| [getting-started](docs/getting-started/install.md) | [Installation](docs/getting-started/install.md) · [configuration (20 env)](docs/getting-started/configure.md) · [wiring up](docs/getting-started/integrate.md) · [live cases](docs/getting-started/cases.md) | a newcomer |
| [integrations](docs/integrations/overview.md) | [Wiring overview](docs/integrations/overview.md) · [zcode](docs/integrations/zcode.md) · [Claude Code](docs/integrations/claude-code.md) · [Cursor](docs/integrations/cursor.md) · [any MCP client](docs/integrations/generic-mcp.md) | when wiring up a harness |
| [architecture](docs/architecture/overview.md) | [Components](docs/architecture/overview.md) · [the cleaning pipeline](docs/architecture/pipeline.md) · [data flows](docs/architecture/data-flow.md) | a contributor |
| [contracts](docs/contracts/mcp-tools.md) | [Tools](docs/contracts/mcp-tools.md) · [output formats](docs/contracts/output-format.md) · [modules](docs/contracts/module-contracts.md) · [configuration](docs/contracts/config.md) | an integrator |
| [operations](docs/operations/runbook.md) | [Runbook](docs/operations/runbook.md) · [token-savings metrics](docs/operations/metrics.md) | operations |
| [product](docs/product/charter.md) | [Charter](docs/product/charter.md) · [features](docs/product/features.md) · [roadmap](docs/product/roadmap.md) · [competitors](docs/product/competitive.md) | the product owner |
| [adr](docs/adr/0001-python-crawl4ai.md) | Six accepted architectural decisions | a contributor |
| [meta](docs/meta/style-guide.md) | [Docs style guide](docs/meta/style-guide.md) · [glossary](docs/meta/glossary.md) | docs authors |

Outside `docs/`: [agents/](agents/) — the subagent, skills, drop-in · [integrations/](integrations/) — custom integrations (hermes, pi, zcode) · [install.sh](install.sh) — the install script · [npm/bathys-mcp/](npm/bathys-mcp/) — the NPM wrapper · [tests/](tests/) — unit tests · [CHANGELOG.md](CHANGELOG.md) — release history.

## 📍 Status

**0.14.1.** Release history: v0.2 "Result Quality" (retries, engine health), v0.3 "Parity with Tavily" (`read_urls`, JSON mode), v0.4 "Operations" (robots ethics, metrics, `bathys-doctor`), v0.5 "Identity & Harness" (repositioning, prompts, subagent), v0.6 "Native Install" (`bathys install`), v0.7 "Ship & Setup" (two-tier extraction, `bathys setup`, the one-liner, uninstall), v0.8 "Engine Orchestration", v0.9 "Borrowed Ideas", v0.10–0.11 "Library Docs" (Phases 1–2), v0.12 "Deep Verdicts" (`source_check` + Phase 3), v0.13 "GitHub Tier & Deep Audit", v0.14 "Backlog Closed" — summaries in [CHANGELOG.md](CHANGELOG.md).

Repository: `github.com/Korrnals/bathys`. Packages published: [PyPI `bathys`](https://pypi.org/project/bathys/) (pip install) and [`bathys-mcp` on npm](https://www.npmjs.com/package/bathys-mcp) (npm install -g); the install one-liner is above. CI and releases run through the cluster release conveyor (`Korrnals/release-pipeline`: verify gates → build → SHA256 → SBOM → cosign + GPG signatures → attach to the GitHub Release; all 15 releases shipped this way — the pre-policy versions retroactively re-attached their signature sets). `.github/workflows/ci.yml` remains the gate description; GitHub Actions itself is disabled account-wide (billing lock), so its failing runs are expected noise. Before 1.0: the first verified run of the Docker image (the Dockerfile ships with the package; the pipeline's docker build phase needs a runner-image update first).

## 🙏 Acknowledgements

Bathys stands on the shoulders of outstanding open projects — thanks to their authors and communities:

- **[SearXNG](https://github.com/searxng/searxng)** — the metasearch engine (AGPL-3.0): Bathys runs it as a separate process and talks to it over a local JSON API; its sources are not modified and are not distributed inside the package.
- **[Crawl4AI](https://github.com/unclecode/crawl4ai)** — the browser tier of extraction (Apache-2.0).
- **[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)** (MIT), **[httpx](https://www.python-httpx.org/)** (BSD-3), **[Playwright](https://playwright.dev/python/)** (Apache-2.0).

Full attributions and license terms for each component — in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## ⚖️ License

Bathys code is [MIT](LICENSE). The components that Bathys installs and uses are licensed separately and listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) (in particular, SearXNG is under AGPL-3.0, with its terms honored).