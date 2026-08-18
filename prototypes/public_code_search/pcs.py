"""pcs — public code search for AI agents (v6, rebuilt from scratch).

Hybrid engine combining, in one tool:
  - GitHub GraphQL  -> repo discovery, repo tree, file blob (field-precise, 1 call)
  - GitHub REST     -> /search/code with text-match snippets + line resolution
  - grep.app REST   -> regex code search across ~1M repos, line numbers, no GitHub auth
  - Sourcegraph GraphQL V2 -> code search *through GraphQL* (line matches, symbols,
                       optional content, LSIF definitions/references)

Mechanisms were extracted from live GitHub source of:
  fulll/github-code-search   (line resolution, 1000-result cap guard)
  janeklb/gh-search          (filter architecture)
  twn39/sourcegraph-search   (Sourcegraph GraphQL V2 queries, code intel)
  bgauryy/octocode           (query validation, scoped-zero repo-state probe)
  zamalali/DeepGit           (cross-encoder rerank, weighted fusion, activity signals)
  github/github-mcp-server   (fields subsetting, minimal output, pagination)
  spences10/mcp-omnisearch   (schema-validated provider responses, unified result shape)
  jasperan/discover-github   (enrichment with API-call caps, exponential backoff)
  JetXu-LLM/llama-github     (per-hit content expansion, outcome enum)

Usage:
  python pcs.py "rate limit retry" --top 10                 # hybrid search (default)
  python pcs.py "fetch repo:fulll/github-code-search"       # repo-scoped
  python pcs.py tree octocode/octocode                      # GraphQL tree
  python pcs.py file fulll/github-code-search src/api.ts    # GraphQL blob
  python pcs.py "definitions references" --engine sourcegraph
  python pcs.py "regex here" --regexp --engine grepapp
  python pcs.py "token budget" --cross-encoder              # optional CE rerank
  python pcs.py "async retry" --deep                        # paginate REST to 1000
  python pcs.py repos "topic:code-search" --sort stars      # repo discovery (GraphQL)
  python pcs.py issues "exponential backoff" --semantic     # issues, incl. semantic
  python pcs.py commits "add retry logic"                   # commit history search
  python pcs.py symbols "fetchWithRetry" --lang go          # symbol search (Sourcegraph)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import subprocess
import sys
import time
from typing import Any

import httpx

# ---------------------------------------------------------------- constants
REST = "https://api.github.com"
GQL = "https://api.github.com/graphql"
GREPAPP = "https://grep.app/api/search"
SOURCEGRAPH = "https://sourcegraph.com/.api/graphql"
UA = "pcs/6.0 (agent code search prototype)"
GH_ACCEPT = "application/vnd.github+json"
GH_TEXTMATCH = "application/vnd.github.text-match+json"

# GitHub caps code search results at 1000 (10 pages x 100) — a hard API
# contract the tool respects to avoid 422 "beyond the first 1000 results".
LINE_HYDRATE_CONCURRENCY = 8
LINE_HYDRATE_TIMEOUT_S = 5.0
QUERY_MAX_CHARS = 256           # GitHub code search query limit

STOPWORDS = {
    "what", "how", "where", "why", "which", "when", "who", "find", "show",
    "give", "get", "do", "does", "is", "are", "was", "were", "to", "the",
    "a", "an", "of", "in", "on", "for", "and", "or", "with", "me", "my",
    "i", "want", "need", "using", "use", "search", "code",
    "people", "someone", "somebody", "anybody", "implement", "implemented",
    "example", "examples", "like", "way", "ways", "thing", "things",
}

_QUALIFIER_RE = re.compile(r"^\s*(repo|org|user|language|path|filename|extension|size|in|is|created|pushed|stars|topics|followers|forks):\S+")
_REGEX_TOKEN_RE = re.compile(r"(?:^|\s)(/(?:[^/\\\r\n]|\\.)+/(?:[imsu]*))(?=$|\s)")

# ---------------------------------------------------------------- errors
class PcsError(Exception):
    def __init__(self, kind: str, message: str, suggestion: str = ""):
        super().__init__(message)
        self.kind = kind
        self.suggestion = suggestion


def _token() -> str:
    tok = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if tok:
        return tok
    try:  # fall back to the gh CLI credential (local ergonomics only)
        tok = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
        if tok:
            return tok
    except Exception:
        pass
    raise PcsError(
        "auth",
        "no GitHub token (GITHUB_TOKEN / GH_TOKEN / gh auth token)",
        "unauthenticated code search is blocked by GitHub; set a token",
    )


# ---------------------------------------------------------------- query compile
def _extract_regex_token(query: str) -> tuple[re.Pattern | None, str]:
    """First boundary-validated /pat/flags token becomes a local regex;
    the rest of the query is passed to providers as plain text."""
    m = _REGEX_TOKEN_RE.search(query)
    if not m:
        return None, query
    try:
        inner = m.group(1)[1:]                 # drop leading /
        pat, flags = inner.rsplit("/", 1)      # /pattern/flags
        re_flags = 0
        re_flags |= re.I if "i" in flags else 0
        re_flags |= re.M if "m" in flags else 0
        re_flags |= re.S if "s" in flags else 0
        rx = re.compile(pat.replace(r"\/", "/"), re_flags)
    except re.error:
        return None, query
    return rx, query[: m.start()] + " " + query[m.end():]


def _qualifier_terms(query: str) -> tuple[list[str], list[str]]:
    """Split qualifiers from free-text terms (octocode: qualifiers pass
    through untouched; free text becomes the keyword clause)."""
    quals, terms = [], []
    for tok in query.split():
        if _QUALIFIER_RE.match(tok):
            quals.append(tok)
        else:
            terms.append(tok)
    return quals, terms


def compile_query(query: str) -> dict[str, Any]:
    """Query -> {api_query, local_regex, repo_scope, has_term}.
    Validation mirrors octocode + fulll: repo requires owner; at least one
    non-qualifier term (or a regex token) must remain."""
    rx, rest = _extract_regex_token(query)
    quals, terms = _qualifier_terms(rest)

    repo = None
    has_owner = any(q.startswith("repo:") for q in quals)
    for q in quals:
        if q.startswith("repo:"):
            repo = q.split(":", 1)[1]
    if repo and "/" not in repo:
        raise PcsError("validation", f"repo scope {repo!r} has no owner",
                       "repository scope requires owner/repo (octocode rule)")

    if not terms and rx is None and not quals:
        raise PcsError("validation", "empty query", "provide at least one term or qualifier")
    if not terms and rx is None and not any(q.startswith(("filename:", "extension:")) for q in quals):
        # fulll rule: qualifier-only queries are rejected by GitHub
        raise PcsError("validation", "qualifier-only query will be rejected by GitHub",
                       "add at least one non-qualifier search term")

    kept = [t for t in terms if t.lower().strip('"\'') not in STOPWORDS]
    if not kept and terms:
        kept = terms[:3]  # never empty a fully-stopword query
    if not kept and rx is not None:
        kept = ["regex"]

    api_query = " ".join(kept + quals)
    if len(api_query) > QUERY_MAX_CHARS:
        api_query = api_query[:QUERY_MAX_CHARS].rsplit(" ", 1)[0]
    clean = [t.strip('"\'') for t in kept if t.strip('"\'')]
    return {"api_query": api_query, "local_regex": rx, "repo_scope": repo,
            "has_owner": has_owner, "terms": clean, "qualifiers": quals}


def refine_variants(compiled: dict[str, Any]) -> list[str]:
    """Deterministic query refinement (DeepGit convert_query angle, no LLM):
    produce provider-shaped variants from the compiled terms."""
    variants = [compiled["api_query"]]  # quote/qualifier-faithful primary
    terms = compiled["terms"]
    quals = " ".join(compiled["qualifiers"])
    if len(terms) > 2:  # tighter recall variant
        variants.append(" ".join(terms[:2]) + (f" {quals}" if quals else ""))
    return [v for v in variants if v.strip()][:2]


# ---------------------------------------------------------------- HTTP core

class Http:
    """Minimal async HTTP client. No rate-limit tracking, no pacing, no
    quota bookkeeping — agents own their own politeness."""

    def __init__(self, token: str):
        self.token = token
        self.client = httpx.AsyncClient(timeout=30, headers={"User-Agent": UA})
        self.stats: dict[str, int] = {"attempted": 0, "failed": 0}

    async def request(self, method: str, url: str, *, headers: dict | None = None,
                      params: dict | None = None, json: dict | None = None) -> httpx.Response:
        self.stats["attempted"] += 1
        resp = await self.client.request(method, url, headers=headers, params=params, json=json)
        if resp.status_code >= 400 and "api.github.com" in url:
            self.stats["failed"] += 1
        return resp

    def gh_headers(self, accept: str = GH_ACCEPT) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}",
                "Accept": accept,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": UA}


# ---------------------------------------------------------------- GraphQL engines
_DISCOVER_QUERY = """
query($q: String!, $n: Int!) {
  search(query: $q, type: REPOSITORY, first: $n) {
    repositoryCount
    nodes { ... on Repository {
      nameWithOwner stargazerCount pushedAt isArchived
      primaryLanguage { name } description
    } }
  }
}
"""

_TREE_QUERY = """
query($owner: String!, $repo: String!) {
  repository(owner: $owner, name: $repo) {
    defaultBranchRef { name }
    object(expression: "HEAD:") { ... on Tree {
      entries { name path type }
    } }
  }
}
"""

_BLOB_QUERY = """
query($owner: String!, $repo: String!, $expr: String!) {
  repository(owner: $owner, name: $repo) {
    object(expression: $expr) { ... on Blob { byteSize isBinary text } }
  }
}
"""




async def gql_search_repos(http: Http, q: str, first: int) -> list[dict]:
    resp = await http.request("POST", GQL,
                              headers=http.gh_headers(),
                              json={"query": _DISCOVER_QUERY, "variables": {"q": q, "n": first}})
    if resp.status_code != 200:
        return []
    data = resp.json()
    nodes = (((data.get("data") or {}).get("search") or {}).get("nodes")) or []
    out = []
    for n in nodes:
        if not n:
            continue
        out.append({
            "repo": n.get("nameWithOwner", ""),
            "stars": n.get("stargazerCount", 0),
            "pushed_at": (n.get("pushedAt") or "")[:10],
            "language": (n.get("primaryLanguage") or {}).get("name") if n.get("primaryLanguage") else None,
            "archived": bool(n.get("isArchived")),
            "description": (n.get("description") or "")[:160],
        })
    return out


async def gql_tree(http: Http, owner: str, repo: str) -> tuple[str, list[dict]]:
    resp = await http.request("POST", GQL,
                              headers=http.gh_headers(),
                              json={"query": _TREE_QUERY, "variables": {"owner": owner, "repo": repo}})
    if resp.status_code != 200:
        raise PcsError("http", f"GraphQL tree failed: {resp.text[:120]}")
    data = resp.json()
    if data.get("errors"):
        raise PcsError("http", f"GraphQL error: {data['errors'][0].get('message','')[:160]}")
    repo_node = ((data.get("data") or {}).get("repository")) or {}
    branch = ((repo_node.get("defaultBranchRef")) or {}).get("name", "HEAD")
    entries = []
    obj = repo_node.get("object") or {}
    for e in obj.get("entries") or []:
        entries.append({"name": e.get("name"), "path": e.get("path"),
                        "type": e.get("type")})
    return branch, entries


async def gql_blob(http: Http, owner: str, repo: str, path: str,
                   max_chars: int = 30000) -> dict:
    resp = await http.request("POST", GQL,
                              headers=http.gh_headers(),
                              json={"query": _BLOB_QUERY,
                                    "variables": {"owner": owner, "repo": repo,
                                                  "expr": f"HEAD:{path}"}})
    if resp.status_code != 200:
        raise PcsError("http", f"GraphQL blob failed: {resp.text[:120]}")
    data = resp.json()
    if data.get("errors"):
        raise PcsError("http", f"GraphQL error: {data['errors'][0].get('message','')[:160]}")
    blob = ((((data.get("data") or {}).get("repository")) or {}).get("object")) or {}
    text = blob.get("text") or ""
    return {"size": blob.get("byteSize", len(text)), "binary": bool(blob.get("isBinary")),
            "text": text[:max_chars], "truncated": len(text) > max_chars}


# ---------------------------------------------------------------- REST code search
def _trim_code_item(item: dict) -> dict:
    """github-mcp-server style fields subsetting: drop everything heavy."""
    repo = item.get("repository") or {}
    tm = []
    for m in item.get("text_matches") or []:
        frag = (m.get("fragment") or "").strip()
        if frag:
            tm.append({"fragment": frag[:240]})
    return {
        "repo": repo.get("full_name", ""),
        "path": item.get("path", ""),
        "sha": item.get("sha", ""),
        "html_url": item.get("html_url", ""),
        "fragments": tm[:2],
        "repo_archived": bool(repo.get("archived")),
    }


async def gh_code_search(http: Http, api_query: str, per_page: int,
                         page: int = 1) -> tuple[list[dict], int]:
    resp = await http.request("GET", f"{REST}/search/code",
                              headers=http.gh_headers(GH_TEXTMATCH),
                              params={"q": api_query, "per_page": per_page, "page": page})
    if resp.status_code == 422:
        raise PcsError("validation", f"GitHub rejected query: {resp.json().get('message','')[:120]}",
                       "check qualifier syntax (repo needs owner; qualifier-only queries rejected)")
    if resp.status_code != 200:
        raise PcsError("http", f"code search HTTP {resp.status_code}: {resp.text[:120]}")
    d = resp.json()
    return [_trim_code_item(it) for it in d.get("items", [])], d.get("total_count", 0)


async def probe_repo_state(http: Http, repo: str) -> str | None:
    """octocode: scoped-zero search is ambiguous -> one cheap probe says why."""
    resp = await http.request("GET", f"{REST}/repos/{repo}",
                              headers=http.gh_headers())
    if resp.status_code == 404:
        return "not_found"
    if resp.status_code == 200:
        d = resp.json()
        if d.get("archived"):
            return "archived"
        if d.get("full_name", "").lower() != repo.lower():
            return f"renamed->{d.get('full_name')}"
    return None


# ---------------------------------------------------------------- grep.app
async def grepapp_search(http: Http, query: str, regexp: bool,
                         language: str | None = None) -> tuple[list[dict], str]:
    """Returns (hits, status). grep.app is Vercel-bot-walled; a 429/403
    checkpoint is surfaced to the agent instead of a silent zero."""
    params: dict[str, Any] = {"q": query, "regexp": "true" if regexp else "false"}
    if language:
        params[f"f.lang.{language}"] = "on"
    resp = await http.request("GET", GREPAPP,
                              params=params)
    if resp.status_code in (403, 429):
        return [], "blocked (Vercel security checkpoint; use the official MCP at mcp.grep.app)"
    if resp.status_code != 200:
        return [], f"http {resp.status_code}"
    try:
        d = resp.json()
    except ValueError:
        return [], "non-JSON response"
    hits = (((d.get("hits")) or {}).get("hits")) or []
    out = []
    for h in hits:
        content = h.get("content") or {}
        snippet = content.get("snippet") or ""
        out.append({
            "repo": h.get("repo", ""),
            "path": h.get("path", ""),
            "branch": h.get("branch", ""),
            "html_url": (f"https://github.com/{h.get('repo')}/blob/{h.get('branch','HEAD')}/{h.get('path','')}"
                         if h.get("repo") and h.get("path") else ""),
            "line": content.get("line_number") or content.get("line"),
            "snippet": snippet[:300],
            "provider": "grep.app",
        })
    return out, ""


# ---------------------------------------------------------------- Sourcegraph (GraphQL V2)
_SG_SEARCH_QUERY = """
query($q: String!) {
  search(query: $q, version: V2, patternType: PATTERNTYPE) {
    results {
      matchCount
      limitHit
      results {
        __typename
        ... on FileMatch {
          repository { name }
          file { path url }
          lineMatches { preview lineNumber offsetAndLengths }
          symbols { name kind containerName url }
        }
      }
    }
  }
}
"""


_SG_INTEL_QUERY = """
query($repo: String!, $rev: String!, $path: String!, $line: Int!, $character: Int!) {
  repository(name: $repo) {
    commit(rev: $rev) {
      blob(path: $path) {
        lsif {
          definitions(line: $line, character: $character) {
            nodes { resource { path repository { name } } range { start { line character } } }
          }
          references(line: $line, character: $character) {
            nodes { resource { path repository { name } } range { start { line character } } }
          }
        }
      }
    }
  }
}
"""


async def sourcegraph_search(http: Http, query: str, regexp: bool) -> tuple[list[dict], str]:
    """Returns (hits, status). Anonymous sourcegraph.com GraphQL is rate
    limited; a silent zero would hide provider outages from the agent."""
    q = _SG_SEARCH_QUERY.replace("PATTERNTYPE", "regexp" if regexp else "literal")
    resp = await http.request("POST", SOURCEGRAPH,
                              headers={"Content-Type": "application/json", "User-Agent": UA},
                              json={"query": q, "variables": {"q": query}})
    if resp.status_code != 200:
        return [], f"http {resp.status_code}"
    data = resp.json()
    if data.get("errors"):
        return [], f"graphql: {(data['errors'][0].get('message') or '')[:80]}"
    results = ((((data.get("data") or {}).get("search")) or {}).get("results")) or {}
    out = []
    for r in results.get("results") or []:
        if not r or r.get("__typename") != "FileMatch":
            continue
        repo = (r.get("repository") or {}).get("name", "")
        f = r.get("file") or {}
        lms = r.get("lineMatches") or []
        repo_clean = repo.removeprefix("github.com/")
        line = lms[0].get("lineNumber") if lms else None
        out.append({
            "repo": repo_clean,
            "path": f.get("path", ""),
            "html_url": (f"https://github.com/{repo_clean}/blob/HEAD/{f.get('path','')}"
                         if repo_clean and f.get("path") else ""),
            "sg_url": f.get("url", ""),
            "line": max(1, int(line)) if line is not None else None,
            "snippet": "\n".join((lm.get("preview") or "")[:120] for lm in lms[:2])[:300],
            "symbols": [f"{s.get('name')}:{s.get('kind','').lower()}" for s in (r.get("symbols") or [])[:3]],
            "provider": "sourcegraph",
        })
    return out, ""


async def sourcegraph_intel(http: Http, repo: str, path: str, line: int,
                            char: int = 1) -> dict:
    resp = await http.request("POST", SOURCEGRAPH,
                              headers={"Content-Type": "application/json", "User-Agent": UA},
                              json={"query": _SG_INTEL_QUERY,
                                    "variables": {"repo": repo, "rev": "HEAD", "path": path,
                                                  "line": line, "character": char}})
    if resp.status_code != 200:
        return {}
    data = resp.json()
    if data.get("errors"):
        return {"error": data["errors"][0].get("message", "")[:200]}
    blob = (((((data.get("data") or {}).get("repository")) or {}).get("commit")) or {}).get("blob") or {}
    lsif = blob.get("lsif") or {}
    out = {"definitions": [], "references": []}
    for kind in ("definitions", "references"):
        for node in ((lsif.get(kind) or {}).get("nodes")) or []:
            res = node.get("resource") or {}
            rng = node.get("range") or {}
            out[kind].append({
                "repo": (res.get("repository") or {}).get("name", ""),
                "path": res.get("path", ""),
                "line": (rng.get("start") or {}).get("line"),
            })
    return out


# ---------------------------------------------------------------- hydration
def _to_raw_url(html_url: str) -> str:
    return (html_url.replace("https://github.com/", "https://raw.githubusercontent.com/")
                    .replace("/blob/", "/"))


def _line_of_fragment(content: str, fragment: str) -> int:
    idx = content.find(fragment)
    if idx == -1:
        return 1
    return content[:idx].count("\n") + 1


async def _hydrate_one(http: Http, hit: dict, sem: asyncio.Semaphore) -> None:
    if hit.get("line") or hit.get("_hydrated") or not hit.get("html_url"):
        return
    hit["_hydrated"] = True
    async with sem:
        try:
            resp = await http.request("GET", _to_raw_url(hit["html_url"]))
            if resp.status_code == 200 and hit.get("fragments"):
                hit["line"] = _line_of_fragment(resp.text, hit["fragments"][0]["fragment"])
        except Exception:
            pass


async def hydrate_lines(http: Http, hits: list[dict]) -> None:
    sem = asyncio.Semaphore(LINE_HYDRATE_CONCURRENCY)
    await asyncio.gather(*(_hydrate_one(http, h, sem) for h in hits))


# ---------------------------------------------------------------- ranking
def _norm(vals: list[float]) -> list[float]:
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return [0.5] * len(vals)
    return [(v - lo) / (hi - lo) for v in vals]


def _lexical_score(query_terms: list[str], hit: dict) -> float:
    """Term presence in path (2.0) and match text (1.0), plus a coverage
    bonus so hits where several terms co-occur beat one-term prose noise."""
    path_l = hit.get("path", "").lower()
    text = (" ".join(f.get("fragment", "") if isinstance(f, dict) else str(f)
                     for f in hit.get("fragments", [])) + " " +
            (hit.get("snippet") or "")).lower()
    score, covered = 0.0, 0
    for t in query_terms:
        tl = t.lower()
        if tl in path_l:
            score += 2.0
            covered += 1
        elif tl in text:
            score += 1.0
            covered += 1
    if query_terms:
        score += (covered / len(query_terms)) * 1.5
    return score


def _recency_score(pushed: str) -> float:
    if not pushed:
        return 0.5
    try:
        days = (time.time() - time.mktime(time.strptime(pushed, "%Y-%m-%d"))) / 86400
        return max(0.0, 1.0 - days / 365)
    except Exception:
        return 0.5


def rank_hits(query_terms: list[str], hits: list[dict],
              repo_meta: dict[str, dict] | None = None,
              local_regex: re.Pattern | None = None,
              cross_encoder: Any | None = None) -> list[dict]:
    if local_regex is not None:
        hits = [h for h in hits
                if local_regex.search((h.get("snippet") or "") + "\n" +
                                      " ".join(f.get("fragment", "") for f in h.get("fragments", [])))]
    if not hits:
        return []
    repo_meta = repo_meta or {}
    stars = []
    for h in hits:
        m = repo_meta.get(h["repo"])
        h["_stars"] = m.get("stars", 0) if m else 0
        h["_pushed"] = m.get("pushed_at", "") if m else ""
        stars.append(math.log(h["_stars"] + 1))
    if cross_encoder is not None:
        pairs, ce_scores = [], []
        for h in hits:
            doc = (h.get("snippet") or " ".join(f.get("fragment", "") for f in h.get("fragments", [])) or h["path"])
            doc = doc[:5000]
            chunks = [doc[i:i + 2000] for i in range(0, len(doc), 2000)] or [doc]
            pairs.extend([(" ".join(query_terms), c) for c in chunks])
        try:
            raw = cross_encoder.predict(pairs, show_progress_bar=False)
        except Exception:
            raw = None
        if raw is not None:
            i = 0
            for h in hits:
                n = max(1, len((h.get("snippet") or "")[:5000]) // 2000 + 1)
                scores = raw[i:i + n]
                ce_scores.append(0.5 * max(scores) + 0.5 * sum(scores) / n)  # DeepGit
                i += n
            if ce_scores and min(ce_scores) < 0:
                shift = -min(ce_scores)
                ce_scores = [s + shift for s in ce_scores]
        if ce_scores:
            lex = [ _lexical_score(query_terms, h) for h in hits]
            st = [math.log(h["_stars"] + 1) for h in hits]
            rec = [_recency_score(h["_pushed"]) for h in hits]
            for h, ce, lx, s, r in zip(hits, _norm(ce_scores), _norm(lex), _norm(st), _norm(rec)):
                h["_score"] = 0.4 * ce + 0.2 * lx + 0.2 * s + 0.2 * r  # DeepGit fusion
                h["_ce"] = round(ce, 4)
            hits.sort(key=lambda h: -h["_score"])
            return hits
    lex = [_lexical_score(query_terms, h) for h in hits]
    prov = [2.0 if h.get("provider") == "gh" else 1.0 for h in hits]
    rec = [_recency_score(h["_pushed"]) for h in hits]
    for h, lx, p, s, r in zip(hits, _norm(lex), _norm(prov), _norm(stars), _norm(rec)):
        h["_score"] = 0.45 * lx + 0.25 * p + 0.2 * s + 0.1 * r
    hits.sort(key=lambda h: -h["_score"])
    return hits


def _load_cross_encoder() -> Any | None:
    try:
        from sentence_transformers import CrossEncoder
        return CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    except Exception:
        return None


# ---------------------------------------------------------------- output
def _fmt_hit(h: dict, i: int) -> str:
    loc = f"L{h['line']}" if h.get("line") else "  "
    frag = (h.get("snippet") or
            (h["fragments"][0]["fragment"] if h.get("fragments") else ""))
    frag = " ".join(frag.split())[:110]
    return (f"{i:>2}. [{loc}] {h['repo']} :: {h['path']}"
            f"{'  (' + h.get('provider','') + ')' if h.get('provider') else ''}\n"
            f"     {frag}")


def _est_tokens(hits: list[dict]) -> int:
    chars = sum(len(_fmt_hit(h, 0)) for h in hits)
    return chars // 4


# ---------------------------------------------------------------- pipeline
async def run_search(http: Http, args: argparse.Namespace) -> int:
    compiled = compile_query(args.query)
    budget = args.budget or 4000
    top = args.top
    eng = args.engine
    run_gh = eng in ("all", "gh")
    run_grep = eng in ("all", "grepapp")
    run_sg = eng in ("all", "sourcegraph")

    # budget allocation (linear): discovery 10%, gh 45%, alt engines 20%, hydrate 25%
    per_page = min(100, max(5, int((budget * 0.45) / 160)))
    hydrate_k = min(top, max(3, int((budget * 0.25) / 600)))

    repo_meta: dict[str, dict] = {}
    discover_task = None
    if not compiled["repo_scope"]:
        qq = " ".join(compiled["terms"][:3])
        discover_task = asyncio.create_task(gql_search_repos(http, qq, first=min(12, top)))

    variants = refine_variants(compiled)
    api_query = variants[0] if variants else compiled["api_query"]

    # local /regex/ token -> alternate engines get the raw pattern in regexp mode
    rx_pat = compiled["local_regex"].pattern if compiled["local_regex"] else None
    grep_q = rx_pat or (" ".join(compiled["terms"][:4]) or api_query)
    sg_q = rx_pat or api_query
    sg_re = args.regexp or rx_pat is not None
    gh_task = (asyncio.create_task(gh_code_search(http, api_query, per_page=per_page))
               if run_gh else None)
    grep_task = (asyncio.create_task(
                     grepapp_search(http, grep_q, regexp=args.regexp or rx_pat is not None))
                 if run_grep else None)
    sg_task = (asyncio.create_task(
                   sourcegraph_search(http, sg_q, regexp=sg_re))
               if run_sg else None)

    # awaits inside try/finally so an early engine failure cancels the rest
    # instead of leaking unretrieved tasks after the client closes
    try:
        gh_hits, gh_total = await gh_task if gh_task else ([], 0)
        if args.deep and gh_hits:  # paginate REST up to the 1000-result cap (fulll)
            pages = min(9, gh_total // per_page)
            for p in range(2, pages + 2):
                extra, _ = await gh_code_search(http, api_query, per_page=per_page, page=p)
                if not extra:
                    break
                gh_hits.extend(extra)
        grep_hits, grep_status = await grep_task if grep_task else ([], "")
        sg_hits, sg_status = await sg_task if sg_task else ([], "")
        if discover_task:
            meta = await discover_task
            repo_meta = {m["repo"]: m for m in meta}
    finally:
        for t in (gh_task, grep_task, sg_task, discover_task):
            if t and not t.done():
                t.cancel()

    for h in gh_hits:
        h["provider"] = "gh"
    for h in sg_hits:
        h["provider"] = "sourcegraph"
    for h in grep_hits:
        h["provider"] = "grep.app"

    hits = gh_hits + grep_hits + sg_hits
    seen, dedup = set(), []
    for h in hits:
        key = (h["repo"], h["path"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(h)

    ce = _load_cross_encoder() if args.cross_encoder else None
    if args.cross_encoder and ce is None:
        print("[warn] cross-encoder unavailable (sentence-transformers missing) "
              "-> lexical rerank only", file=sys.stderr)
    ranked = rank_hits(compiled["terms"], dedup, repo_meta, compiled["local_regex"], ce)[:top]

    await hydrate_lines(http, ranked[:hydrate_k])

    # scoped-zero probe (octocode)
    state = None
    if not gh_hits and compiled["repo_scope"]:
        state = await probe_repo_state(http, compiled["repo_scope"])

    # output
    if args.json:
        print(json.dumps({
            "query": args.query,
            "compiled": api_query,
            "total_gh": gh_total,
            "hits": [{k: v for k, v in h.items() if not k.startswith("_")} for h in ranked],
            "repo_state": state,
            "grepapp_status": grep_status,
            "sourcegraph_status": sg_status,
        }, indent=2))
    else:
        print(f"pcs v6  query={args.query!r}  compiled={api_query!r}")
        print(f"  gh={len(gh_hits)}(total {gh_total})  grep.app={len(grep_hits)}"
              f"{' [' + grep_status + ']' if grep_status else ''}  "
              f"sourcegraph={len(sg_hits)}"
              f"{' [' + sg_status + ']' if sg_status else ''}  ranked={len(ranked)}")
        if state:
            print(f"  [!] scoped repo probe: {state}")
        for i, h in enumerate(ranked, 1):
            print(_fmt_hit(h, i))
        print(f"est tokens out: {_est_tokens(ranked)}  "
              f"calls: {http.stats['attempted']} attempted, "
              f"{http.stats['failed']} failed")
    return 0


async def run_tree(http: Http, args: argparse.Namespace) -> int:
    owner, _, repo = args.target.partition("/")
    branch, entries = await gql_tree(http, owner, repo)
    dirs = sorted(e["path"] for e in entries if e["type"] == "tree")
    files = sorted(e["path"] for e in entries if e["type"] == "blob")
    print(f"tree {owner}/{repo} @ {branch}: {len(dirs)} dirs, {len(files)} files")
    for p in dirs[:args.limit]:
        print(f"  d {p}/")
    for p in files[:args.limit]:
        print(f"  f {p}")
    return 0


async def run_file(http: Http, args: argparse.Namespace) -> int:
    owner, _, repo = args.target.partition("/")
    blob = await gql_blob(http, owner, repo, args.path, max_chars=args.max_chars)
    print(f"blob {owner}/{repo}/{args.path}  size={blob['size']}B "
          f"binary={blob['binary']} truncated={blob['truncated']}")
    print(blob["text"])
    return 0


async def run_intel(http: Http, args: argparse.Namespace) -> int:
    owner, _, repo = args.target.partition("/")
    d = await sourcegraph_intel(http, f"github.com/{owner}/{repo}", args.path, args.line)
    if not d:
        print("no LSIF data (index unavailable or anonymous rate limit)")
        return 1
    for kind in ("definitions", "references"):
        print(f"{kind} ({len(d.get(kind, []))}):")
        for n in d.get(kind, [])[:10]:
            print(f"  {n['repo']} :: {n['path']}#L{n.get('line')}")
    return 0




async def run_repos(http: Http, args: argparse.Namespace) -> int:
    """First-class repo discovery via GraphQL (the structured half of the hybrid)."""
    q = args.query if args.sort == "best" else f"{args.query} sort:{args.sort}"
    repos = await gql_search_repos(http, q, first=args.n)
    if args.json:
        print(json.dumps(repos, indent=2))
        return 0
    print(f"repos matching {args.query!r} ({len(repos)}):")
    for r in repos:
        flags = " [archived]" if r["archived"] else ""
        print(f"  {r['stars']:>6}  {r['repo']}{flags}  ({r['language'] or '?'})  "
              f"pushed {r['pushed_at']}")
        if r["description"]:
            print(f"           {r['description']}")
    return 0


async def run_issues(http: Http, args: argparse.Namespace) -> int:
    """Issue search incl. GitHub's semantic/hybrid modes (apiVersion 2026-03-10)."""
    q = f"{args.query} state:{args.state}" if args.state else args.query
    mode = args.mode or ("semantic" if args.semantic else ("hybrid" if args.hybrid else None))
    params: dict[str, Any] = {"q": q, "per_page": args.n}
    if mode:
        params["search_type"] = mode
    resp = await http.request("GET", f"{REST}/search/issues",
                              headers=http.gh_headers(GH_TEXTMATCH), params=params)
    if resp.status_code != 200:
        raise PcsError("http", f"issue search HTTP {resp.status_code}: {resp.text[:150]}")
    d = resp.json()
    items = []
    for it in d.get("items", []):
        frags = [m.get("fragment", "").strip() for m in (it.get("text_matches") or [])]
        repo_url = it.get("repository_url") or ""
        items.append({
            "repo": repo_url.removeprefix("https://api.github.com/repos/"),
            "number": it.get("number"),
            "title": it.get("title", ""),
            "state": it.get("state"),
            "comments": it.get("comments"),
            "html_url": it.get("html_url", ""),
            "labels": [l.get("name") for l in (it.get("labels") or [])][:4],
            "snippet": (frags[0] if frags else (it.get("body") or ""))[:220],
        })
    if args.json:
        print(json.dumps({"total": d.get("total_count"), "items": items}, indent=2))
        return 0
    print(f"issues matching {args.query!r} ({d.get('total_count', '?')} total):")
    for it in items:
        lab = f" [{', '.join(it['labels'])}]" if it["labels"] else ""
        print(f"  #{it['number']} {it['state']:<6} {it['repo']} — {it['title']}{lab}")
        if it["snippet"]:
            print(f"       {' '.join(it['snippet'].split())[:160]}")
    return 0


async def run_commits(http: Http, args: argparse.Namespace) -> int:
    resp = await http.request("GET", f"{REST}/search/commits",
                              headers=http.gh_headers(GH_TEXTMATCH),
                              params={"q": args.query, "per_page": args.n})
    if resp.status_code != 200:
        raise PcsError("http", f"commit search HTTP {resp.status_code}: {resp.text[:150]}")
    d = resp.json()
    items = []
    for it in d.get("items", []):
        c = it.get("commit") or {}
        frags = [m.get("fragment", "").strip() for m in (it.get("text_matches") or [])]
        items.append({
            "sha": it.get("sha", "")[:8],
            "message": (c.get("message") or "").split("\n")[0][:100],
            "author": ((c.get("author") or {}).get("name") or
                       (c.get("author") or {}).get("login") or "?"),
            "date": (c.get("author") or {}).get("date", "")[:10],
            "repo": (it.get("repository") or {}).get("full_name", ""),
            "html_url": it.get("html_url", ""),
            "snippet": frags[0] if frags else "",
        })
    if args.json:
        print(json.dumps({"total": d.get("total_count"), "items": items}, indent=2))
        return 0
    print(f"commits matching {args.query!r} ({d.get('total_count', '?')} total):")
    for it in items:
        print(f"  {it['sha']} {it['repo']} {it['date']} {it['author']}")
        print(f"       {it['message']}")
        if it["snippet"]:
            print(f"       {' '.join(it['snippet'].split())[:140]}")
    return 0


_SG_SYMBOL_QUERY = """
query($q: String!) {
  search(query: $q, version: V2) {
    results {
      matchCount
      results {
        __typename
        ... on FileMatch {
          repository { name }
          file { path }
          symbols { name kind language containerName location { range { start { line } } } }
        }
      }
    }
  }
}
"""


async def run_symbols(http: Http, args: argparse.Namespace) -> int:
    """Symbol search across public code via Sourcegraph type:symbol."""
    q = f"context:global type:symbol {args.query} count:{min(args.n * 2, 50)}"
    if args.lang:
        q += f" lang:{args.lang}"
    resp = await http.request("POST", SOURCEGRAPH,
                              headers={"Content-Type": "application/json", "User-Agent": UA},
                              json={"query": _SG_SYMBOL_QUERY, "variables": {"q": q}})
    if resp.status_code != 200:
        raise PcsError("http", f"symbol search HTTP {resp.status_code}: {resp.text[:150]}")
    data = resp.json()
    if data.get("errors"):
        raise PcsError("http", f"symbol search: {data['errors'][0].get('message','')[:150]}")
    results = ((((data.get("data") or {}).get("search")) or {}).get("results")) or {}
    syms = []
    for r in results.get("results") or []:
        if not r or r.get("__typename") != "FileMatch":
            continue
        repo = ((r.get("repository") or {}).get("name") or "").removeprefix("github.com/")
        path = (r.get("file") or {}).get("path", "")
        for s in r.get("symbols") or []:
            loc = ((s.get("location") or {}).get("range") or {}).get("start") or {}
            line = loc.get("line")
            syms.append({
                "name": s.get("name"),
                "kind": (s.get("kind") or "").lower(),
                "language": s.get("language"),
                "container": s.get("containerName"),
                "repo": repo,
                "path": path,
                "line": (line + 1) if line is not None else None,  # Sourcegraph 0-based
                "url": (f"https://github.com/{repo}/blob/HEAD/{path}#L{(line + 1) if line is not None else 1}"
                        if repo and path else ""),
            })
    if args.json:
        print(json.dumps({"count": len(syms), "symbols": syms}, indent=2))
        return 0
    print(f"symbols matching {args.query!r} ({len(syms)}):")
    for s in syms[:args.n]:
        loc = f"L{s['line']}" if s.get("line") else ""
        print(f"  {s['name']} [{s['kind'] or '?'}] {s['language'] or '?'}  "
              f"{s['repo']} :: {s['path']} {loc}")
        if s.get("container"):
            print(f"       in {s['container']}")
    return 0



# ---------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="pcs — hybrid public code search for AI agents")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--budget", type=int, default=4000, help="token budget (rough)")
    s.add_argument("--engine", choices=["all", "gh", "grepapp", "sourcegraph"], default="all")
    s.add_argument("--regexp", action="store_true", help="treat query as regex for alt engines")
    s.add_argument("--cross-encoder", action="store_true")
    s.add_argument("--deep", action="store_true", help="paginate REST up to 1000 results")
    s.add_argument("--json", action="store_true")
    t = sub.add_parser("tree")
    t.add_argument("target")
    t.add_argument("--limit", type=int, default=60)
    f = sub.add_parser("file")
    f.add_argument("target")
    f.add_argument("path")
    f.add_argument("--max-chars", type=int, default=30000)
    i = sub.add_parser("intel")
    i.add_argument("target")
    i.add_argument("path")
    i.add_argument("line", type=int)
    r = sub.add_parser("repos")
    r.add_argument("query")
    r.add_argument("--n", type=int, default=10)
    r.add_argument("--sort", choices=["best", "stars", "updated", "forks"], default="best")
    r.add_argument("--json", action="store_true")
    iss = sub.add_parser("issues")
    iss.add_argument("query")
    iss.add_argument("--n", type=int, default=10)
    iss.add_argument("--mode", choices=["semantic", "hybrid"], default=None)
    iss.add_argument("--semantic", action="store_true")
    iss.add_argument("--hybrid", action="store_true")
    iss.add_argument("--state", choices=["open", "closed"], default=None)
    iss.add_argument("--json", action="store_true")
    c = sub.add_parser("commits")
    c.add_argument("query")
    c.add_argument("--n", type=int, default=10)
    c.add_argument("--json", action="store_true")
    sy = sub.add_parser("symbols")
    sy.add_argument("query")
    sy.add_argument("--n", type=int, default=10)
    sy.add_argument("--lang")
    sy.add_argument("--json", action="store_true")
    return ap


async def main() -> int:
    ap = build_parser()
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 1
    try:
        http = Http(_token())
        try:
            if args.cmd == "search":
                return await run_search(http, args)
            if args.cmd == "tree":
                return await run_tree(http, args)
            if args.cmd == "repos":
                return await run_repos(http, args)
            if args.cmd == "issues":
                return await run_issues(http, args)
            if args.cmd == "commits":
                return await run_commits(http, args)
            if args.cmd == "symbols":
                return await run_symbols(http, args)
            if args.cmd == "file":
                return await run_file(http, args)
            return await run_intel(http, args)
        finally:
            await http.client.aclose()
    except PcsError as e:
        print(f"[{e.kind}] {e}", file=sys.stderr)
        if e.suggestion:
            print(f"  hint: {e.suggestion}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
