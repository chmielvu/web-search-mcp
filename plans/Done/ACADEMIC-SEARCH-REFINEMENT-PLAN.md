# Academic Search Tool Refinement Plan

## Research Synthesis

### Reference Implementations Analyzed

| Repository | Stars | Key Patterns |
|------------|-------|--------------|
| **paper-search-mcp** (openags) | 1,451 | 28 providers, `PaperSource` base class, `asyncio.to_thread()` wrapper |
| **academic-search-mcp-server** (afrise) | 112 | S2 + Crossref via `httpx.AsyncClient`, native async |
| **pyalex** (J535D165) | 379 | OpenAlex SDK with retry config, `AlexConfig` pattern |
| **habanero** (sckott) | Active | CrossRef via `Crossref().works(ids=doi)` |

### Current Implementation Issues

| Issue | Root Cause | Fix |
|-------|------------|-----|
| S2 SDK hangs on rate limit | Sync SDK in `asyncio.to_thread`, no `with_retry=False` | Use `AsyncSemanticScholar` or direct HTTP |
| Empty results when both sources queried | Failure propagates without partial results | Return ArXiv + warning when S2 fails |
| No timeout on SDK | `SemanticScholar(timeout=30)` missing in code | Add explicit timeout |
| No API key for S2 | `KINDLY_S2_API_KEY` not documented | Document optional key |

---

## Phase 1: Fix Semantic Scholar Integration (Immediate)

### 1.1 Switch to Native Async

**Current (problematic):**
```python
sch = SemanticScholar(api_key=api_key)  # Sync SDK
await asyncio.to_thread(_sync_search)   # Blocks thread
```

**Recommended (native async):**
```python
from semanticscholar import AsyncSemanticScholar

async def search_semanticscholar(...):
    sch = AsyncSemanticScholar(timeout=30)
    try:
        results = await sch.search_paper(
            query,
            limit=overfetch,
            timeout=30,
            # Don't use SDK retry - we have our own
        )
    except Exception as e:
        logger.warning(f"S2 search failed: {e}")
        return []  # Return empty, not raise
```

### 1.2 Add Fail-Fast Configuration

```python
# In academic_s2.py
S2_TIMEOUT = int(os.environ.get("KINDLY_S2_TIMEOUT", "30"))
S2_MAX_RETRIES = int(os.environ.get("KINDLY_S2_MAX_RETRIES", "0"))  # Fail fast
```

### 1.3 Fix Partial Result Handling

**Current bug in orchestrator:**
```python
# Returns empty when S2 fails
if not result_lists:
    return AcademicSearchResponse(results=[], ...)
```

**Fix:**
```python
# Always return what we have + warnings
merged = _merge_papers(result_lists)  # May have ArXiv results
return AcademicSearchResponse(
    results=merged[:limit],
    warnings=warnings,  # Include S2 failure warning
)
```

---

## Phase 2: Add OpenAlex Provider (High Priority)

### 2.1 Why OpenAlex First

| Metric | OpenAlex | Semantic Scholar |
|--------|----------|------------------|
| Coverage | 250M works | 214M papers |
| Sources | All major databases | Primary papers |
| Free API Key | Yes (polite pool) | Optional |
| Entity Types | 16 (works, authors, institutions...) | Papers, authors |
| SDK Quality | `pyalex` (stable, async-ready) | `semanticscholar` (sync issues) |

### 2.2 Implementation Pattern (from paper-search-mcp)

```python
# src/kindly_web_search_mcp_server/search/academic_openalex.py

import pyalex
from ..models import AcademicPaper

pyalex.config.email = os.environ.get("KINDLY_OPENALEX_EMAIL", "")
pyalex.config.api_key = os.environ.get("KINDLY_OPENALEX_API_KEY", "")

async def search_openalex(
    query: str,
    *,
    limit: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
    fields_of_study: list[str] | None = None,
    open_access_only: bool = False,
) -> list[AcademicPaper]:
    """Search OpenAlex via pyalex library."""
    
    # Build filter
    filters = {}
    if year_from:
        filters["from_publication_date"] = f"{year_from}-01-01"
    if year_to:
        filters["to_publication_date"] = f"{year_to}-12-31"
    if open_access_only:
        filters["is_oa"] = True
    
    def _sync_search():
        works = pyalex.Works()
        if filters:
            works = works.filter(**filters)
        results = works.search(query).get(per_page=limit * 2)
        
        papers = []
        for w in results:
            paper = _normalize_openalex(w)
            if paper:
                papers.append(paper)
        return papers[:limit]
    
    return await asyncio.to_thread(_sync_search)

def _normalize_openalex(work: dict) -> AcademicPaper | None:
    """Normalize OpenAlex work to AcademicPaper."""
    if not work.get("title"):
        return None
    
    return AcademicPaper(
        title=work["title"],
        authors=[a["display_name"] for a in work.get("authorships", [])],
        abstract=work.get("abstract") or None,
        year=work.get("publication_year"),
        venue=work.get("primary_source", {}).get("display_name"),
        citations=work.get("cited_by_count", 0),
        url=work.get("id") or f"https://openalex.org/{work['id']}",
        pdf_url=work.get("open_access", {}).get("oa_url"),
        source="openalex",
        source_id=work.get("id", "").replace("https://openalex.org/", ""),
        external_ids=_extract_external_ids(work),
        fields_of_study=work.get("topics", []),
        is_open_access=work.get("open_access", {}).get("is_oa", False),
    )
```

### 2.3 External ID Mapping

```python
def _extract_external_ids(work: dict) -> dict[str, str]:
    """Extract DOI, ArXiv, PubMed IDs from OpenAlex work."""
    ids = work.get("ids", {})
    ext = {}
    if ids.get("doi"):
        ext["DOI"] = ids["doi"].replace("https://doi.org/", "")
    if ids.get("pmid"):
        ext["PubMed"] = ids["pmid"]
    if ids.get("arxiv"):
        ext["ArXiv"] = ids["arxiv"]
    return ext if ext else None
```

---

## Phase 3: Add CrossRef Provider (Medium Priority)

### 3.1 Purpose: DOI Enrichment + Citation Counts

CrossRef is best for:
- DOI-based metadata lookup
- Citation counts (via `is-referenced-by-count`)
- Reference lists
- Bibliographic data (publisher, type, ISSN)

### 3.2 Implementation (via habanero)

```python
# src/kindly_web_search_mcp_server/search/academic_crossref.py

from habanero import Crossref

async def search_crossref(
    query: str,
    *,
    limit: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
    has_doi: bool = True,  # CrossRef returns DOI by default
) -> list[AcademicPaper]:
    """Search CrossRef via habanero library."""
    
    cr = Crossref(mailto=os.environ.get("CROSSREF_MAILTO", ""))
    
    def _sync_search():
        filters = {}
        if year_from:
            filters["from-pub-date"] = str(year_from)
        if year_to:
            filters["until-pub-date"] = str(year_to)
        
        result = cr.works(query=query, limit=limit * 2, filter=filters)
        
        papers = []
        for item in result.get("message", {}).get("items", []):
            paper = _normalize_crossref(item)
            if paper:
                papers.append(paper)
        return papers[:limit]
    
    return await asyncio.to_thread(_sync_search)

def _normalize_crossref(item: dict) -> AcademicPaper | None:
    """Normalize CrossRef work to AcademicPaper."""
    title = item.get("title", [])
    if not title:
        return None
    
    return AcademicPaper(
        title=title[0] if isinstance(title, list) else title,
        authors=[a.get("given", "") + " " + a.get("family", "") 
                 for a in item.get("author", [])],
        abstract=None,  # CrossRef doesn't provide abstracts
        year=item.get("published", {}).get("date-parts", [[None]])[0][0],
        venue=item.get("container-title", [None])[0],
        citations=item.get("is-referenced-by-count", 0),
        url=item.get("URL"),
        pdf_url=None,  # CrossRef doesn't have PDF URLs
        source="crossref",
        source_id=item.get("DOI", ""),
        external_ids={"DOI": item.get("DOI", "")},
        fields_of_study=None,
        is_open_access=None,
    )
```

---

## Phase 4: Add PubMed Provider (Medium Priority)

### 4.1 Purpose: Biomedical Literature

PubMed covers:
- 35M+ biomedical citations
- MEDLINE database
- Clinical trials, case reports

### 4.2 Implementation (NCBI E-utilities pattern)

```python
# src/kindly_web_search_mcp_server/search/academic_pubmed.py

import requests
from xml.etree import ElementTree as ET

PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

async def search_pubmed(
    query: str,
    *,
    limit: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
) -> list[AcademicPaper]:
    """Search PubMed via NCBI E-utilities API."""
    
    api_key = os.environ.get("PUBMED_API_KEY", "")
    
    def _sync_search():
        # Step 1: Search for IDs
        search_params = {
            "db": "pubmed",
            "term": _build_pubmed_query(query, year_from, year_to),
            "retmax": limit * 2,
            "retmode": "xml",
            "api_key": api_key,
        }
        search_resp = requests.get(PUBMED_SEARCH_URL, params=search_params, timeout=30)
        search_root = ET.fromstring(search_resp.content)
        ids = [id_elem.text for id_elem in search_root.findall(".//Id") if id_elem.text]
        
        if not ids:
            return []
        
        # Step 2: Fetch metadata
        fetch_params = {
            "db": "pubmed",
            "id": ",".join(ids),
            "retmode": "xml",
            "api_key": api_key,
        }
        fetch_resp = requests.get(PUBMED_FETCH_URL, params=fetch_params, timeout=60)
        fetch_root = ET.fromstring(fetch_resp.content)
        
        papers = []
        for article in fetch_root.findall(".//PubmedArticle"):
            paper = _parse_pubmed_article(article)
            if paper:
                papers.append(paper)
        return papers[:limit]
    
    return await asyncio.to_thread(_sync_search)

def _build_pubmed_query(query: str, year_from: int | None, year_to: int | None) -> str:
    """Build PubMed query with date filter."""
    if year_from and year_to:
        return f"{query} AND {year_from}:{year_to}[dp]"
    elif year_from:
        return f"{query} AND {year_from}[dp]"
    return query

def _parse_pubmed_article(article) -> AcademicPaper | None:
    """Parse PubMed XML article to AcademicPaper."""
    pmid = article.find(".//PMID")
    if not pmid or not pmid.text:
        return None
    
    title_elem = article.find(".//ArticleTitle")
    title = "".join(title_elem.itertext()).strip() if title_elem else ""
    if not title:
        return None
    
    authors = []
    for author in article.findall(".//Author"):
        name = author.find("ForeName")
        last = author.find("LastName")
        if name and last:
            authors.append(f"{name.text} {last.text}")
    
    year_elem = article.find(".//PubDate/Year")
    year = int(year_elem.text) if year_elem and year_elem.text else None
    
    doi_elem = article.find(".//ArticleId[@IdType='doi']")
    doi = doi_elem.text if doi_elem else None
    
    return AcademicPaper(
        title=title,
        authors=authors,
        abstract=_extract_abstract(article),
        year=year,
        venue=article.find(".//Journal/Title"),
        citations=None,  # PubMed doesn't provide citation counts
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid.text}/",
        pdf_url=None,
        source="pubmed",
        source_id=pmid.text,
        external_ids={"PubMed": pmid.text, "DOI": doi} if doi else {"PubMed": pmid.text},
        fields_of_study=["Medicine"],
        is_open_access=None,
    )
```

---

## Phase 5: Add CORE Provider (Lower Priority)

### 5.1 Purpose: Open Access Full-Text

CORE aggregates:
- Open access papers from repositories worldwide
- Full-text availability
- Repository metadata

### 5.2 Implementation (CORE API v3)

```python
# src/kindly_web_search_mcp_server/search/academic_core.py

CORE_API_URL = "https://api.core.ac.uk/v3"

async def search_core(
    query: str,
    *,
    limit: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
    has_fulltext: bool = True,  # CORE specializes in full-text
) -> list[AcademicPaper]:
    """Search CORE for open access papers."""
    
    api_key = os.environ.get("CORE_API_KEY", "")
    
    def _sync_search():
        headers = {"Authorization": f"Bearer {api_key}"}
        params = {
            "q": query,
            "limit": limit * 2,
        }
        if year_from:
            params["year_from"] = year_from
        if year_to:
            params["year_to"] = year_to
        if has_fulltext:
            params["has_fulltext"] = "true"
        
        resp = requests.get(
            f"{CORE_API_URL}/search",
            headers=headers,
            params=params,
            timeout=30,
        )
        
        if resp.status_code == 429:
            logger.warning("CORE rate limited")
            return []
        
        data = resp.json()
        papers = []
        for item in data.get("results", []):
            paper = _normalize_core(item)
            if paper:
                papers.append(paper)
        return papers[:limit]
    
    return await asyncio.to_thread(_sync_search)
```

---

## Architecture Changes

### Unified Provider Registry

```python
# src/kindly_web_search_mcp_server/search/academic_providers.py

PROVIDERS = {
    "arxiv": {"fn": search_arxiv, "requires": [], "specialties": ["cs", "physics", "math"]},
    "semanticscholar": {"fn": search_semanticscholar, "requires": ["KINDLY_S2_API_KEY"], "specialties": ["citations", "ai"]},
    "openalex": {"fn": search_openalex, "requires": [], "specialties": ["comprehensive", "institutions"]},
    "crossref": {"fn": search_crossref, "requires": [], "specialties": ["doi", "bibliographic"]},
    "pubmed": {"fn": search_pubmed, "requires": [], "specialties": ["biomedical", "clinical"]},
    "core": {"fn": search_core, "requires": ["CORE_API_KEY"], "specialties": ["fulltext", "open_access"]},
}

def resolve_sources(sources: list[str] | None, query_context: str) -> list[str]:
    """Resolve which sources to query based on request and context."""
    if sources is None:
        # Default: arxiv + openalex (both free, comprehensive)
        return ["arxiv", "openalex"]
    
    # Map user input to canonical names
    normalized = set()
    for s in sources:
        s_lower = s.lower().replace("-", "").replace("_", "").replace(" ", "")
        if s_lower in ("arxiv",):
            normalized.add("arxiv")
        elif s_lower in ("s2", "semantic", "semanticscholar"):
            normalized.add("semanticscholar")
        elif s_lower in ("openalex", "alex", "oa"):
            normalized.add("openalex")
        elif s_lower in ("crossref", "cr", "doi"):
            normalized.add("crossref")
        elif s_lower in ("pubmed", "pm", "medline"):
            normalized.add("pubmed")
        elif s_lower in ("core", "oa"):
            normalized.add("core")
    
    return list(normalized) if normalized else ["arxiv", "openalex"]
```

### Enhanced Deduplication

```python
# Cross-source deduplication priority
DEDUP_PRIORITY = {
    # DOI is universal identifier - highest priority
    "doi": lambda p: p.external_ids.get("DOI"),
    # ArXiv ID - second priority
    "arxiv": lambda p: p.external_ids.get("ArXiv"),
    # PubMed ID - for biomedical papers
    "pmid": lambda p: p.external_ids.get("PubMed"),
    # Title fuzzy match - last resort
    "title": lambda p: p.title.lower().strip(),
}
```

---

## Environment Variables

```bash
# .env.example additions

# Academic Search Providers
# Semantic Scholar (optional, 100 RPS with key vs 1 RPS shared)
KINDLY_S2_API_KEY=
KINDLY_S2_TIMEOUT=30
KINDLY_S2_MAX_RETRIES=0  # Fail fast, we handle retries

# OpenAlex (optional, polite pool with email)
KINDLY_OPENALEX_EMAIL=
KINDLY_OPENALEX_API_KEY=

# CrossRef (optional, polite pool with mailto)
CROSSREF_MAILTO=

# PubMed (optional, higher rate limit with key)
PUBMED_API_KEY=

# CORE (optional, required for full-text search)
CORE_API_KEY=

# Academic search defaults
KINDLY_ACADEMIC_DEFAULT_SOURCES=arxiv,openalex
KINDLY_ACADEMIC_MAX_RESULTS=10
```

---

## Implementation Schedule

| Phase | Task | Priority | Estimated Time |
|-------|------|----------|----------------|
| 1.1 | Switch S2 to AsyncSemanticScholar | Critical | 1 hour |
| 1.2 | Add S2 fail-fast config | Critical | 30 min |
| 1.3 | Fix partial result handling | Critical | 30 min |
| 2.1 | Add OpenAlex provider | High | 2 hours |
| 2.2 | Add OpenAlex tests | High | 1 hour |
| 3.1 | Add CrossRef provider | Medium | 2 hours |
| 3.2 | Add CrossRef tests | Medium | 1 hour |
| 4.1 | Add PubMed provider | Medium | 2 hours |
| 4.2 | Add PubMed tests | Medium | 1 hour |
| 5.1 | Add CORE provider | Lower | 2 hours |
| 5.2 | Add CORE tests | Lower | 1 hour |
| - | Update orchestrator registry | Medium | 1 hour |
| - | Update deduplication logic | Medium | 1 hour |
| - | Update .env.example | Low | 15 min |
| - | Update documentation | Low | 1 hour |

**Total Estimated Time: ~14 hours**

---

## Testing Strategy

### Unit Tests per Provider

```python
# tests/test_academic_openalex.py
def test_openalex_search_basic():
    """Test basic OpenAlex search returns results."""
    
def test_openalex_year_filter():
    """Test year_from/year_to filters work."""
    
def test_openalex_normalization():
    """Test OpenAlex work normalized correctly to AcademicPaper."""
    
def test_openalex_external_ids():
    """Test DOI, ArXiv, PubMed IDs extracted from OpenAlex."""
    
def test_openalex_rate_limit_handling():
    """Test graceful handling when rate limited."""
```

### Integration Tests

```python
# tests/test_academic_orchestrator.py
def test_multi_source_search():
    """Test search across multiple sources returns merged results."""
    
def test_partial_results_on_failure():
    """Test ArXiv results returned when S2 fails."""
    
def test_dedup_cross_source():
    """Test deduplication across DOI, ArXiv ID, title."""
    
def test_source_selection():
    """Test source parameter resolution."""
```

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Coverage (works indexed) | ~216M (S2 + ArXiv) | ~500M (all providers) |
| Open access papers | ~2.5M (ArXiv only) | ~50M (OpenAlex + CORE) |
| Biomedical coverage | 0 | 35M (PubMed) |
| DOI enrichment | Partial | Full (CrossRef) |
| Citation counts | S2 only | S2 + OpenAlex + CrossRef |
| Success rate on empty results | 0% (bug) | 100% (ArXiv fallback) |

---

## References

1. **paper-search-mcp**: https://github.com/openags/paper-search-mcp (28 providers, 1451 stars)
2. **pyalex**: https://github.com/J535D165/pyalex (OpenAlex SDK)
3. **habanero**: https://github.com/sckott/habanero (CrossRef SDK)
4. **Semantic Scholar SDK**: https://github.com/danielnsilva/semanticscholar (async issues PR #116)
5. **OpenAlex API**: https://docs.openalex.org/api-reference/introduction
6. **CORE API**: https://api.core.ac.uk/docs/v3
7. **PubMed E-utilities**: https://www.ncbi.nlm.nih.gov/home/develop/api
8. **CrossRef API**: https://api.crossref.org/swagger