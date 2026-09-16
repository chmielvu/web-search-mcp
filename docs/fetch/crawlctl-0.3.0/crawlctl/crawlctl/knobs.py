"""Config builders ("knobs") for the Crawl4AI REST API + URL discovery.

Everything the server accepts is serialized here with the exact envelope the
Docker API expects (verified against server sources 0.6.3-0.9.3):

* top-level scalars are passed through as flat kwargs;
* nested objects / strategies use the ``{"type": "Name", "params": {...}}``
  envelope (PruningContentFilter, BM25ContentFilter, DefaultMarkdownGenerator);
* enums use ``{"type": "CacheMode", "params": "bypass"}``.

Version gating (ServerProfile.capabilities):
* >= 0.9  "untrusted" gate -> js_code/session/deep-crawl/proxy fields are
  stripped with a warning, page_timeout clamped to <= 60000 ms;
* >= 0.7  content filters live inside ``markdown_generator`` (older builds
  want top-level ``content_filter`` + ``fit_markdown``);
* >= 0.8  ``target_elements`` available.
"""

from __future__ import annotations

import gzip
import re
import zlib
from fnmatch import fnmatch
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from .client import Crawl4AIClient, ServerProfile

# --------------------------------------------------------------------------
# RAG presets (server-side DOM scoping tuned per content type; see RESEARCH.md)

DEFAULT_EXCLUDED_SELECTOR = (
    "nav, footer, aside, form, .sidebar, .toc, .breadcrumb, .breadcrumbs, "
    ".cookie-banner, .cookie-consent, .social-share, .related-posts, "
    ".pagination, .comment-form, #footer, #sidebar, #nav"
)

PRESETS: Dict[str, Dict[str, Any]] = {
    "docs": {
        "word_count_threshold": 10,
        "excluded_selector": DEFAULT_EXCLUDED_SELECTOR,
        "pruning": {"threshold": 0.45, "threshold_type": "dynamic"},
    },
    "wiki": {
        "word_count_threshold": 20,
        "excluded_selector": DEFAULT_EXCLUDED_SELECTOR
        + ", .infobox, .navbox, .mw-editsection, .reflist",
        "pruning": {"threshold": 0.48, "threshold_type": "dynamic"},
    },
    "blog": {
        "word_count_threshold": 10,
        "excluded_selector": DEFAULT_EXCLUDED_SELECTOR
        + ", .comments, .author-bio, .newsletter-signup",
        "pruning": {"threshold": 0.48, "threshold_type": "dynamic"},
    },
    "news": {
        "word_count_threshold": 10,
        "excluded_selector": DEFAULT_EXCLUDED_SELECTOR
        + ", .related-articles, .trending, .live-updates",
        "pruning": {"threshold": 0.48, "threshold_type": "dynamic"},
    },
    "generic": {
        "word_count_threshold": 15,
        "excluded_selector": DEFAULT_EXCLUDED_SELECTOR,
        "pruning": {"threshold": 0.48, "threshold_type": "dynamic"},
    },
}


def rag_preset(content_type: str) -> Dict[str, Any]:
    return dict(PRESETS.get(content_type, PRESETS["generic"]))


# --------------------------------------------------------------------------
# envelope helpers

def _wrapped(type_name: str, params: Any) -> Dict[str, Any]:
    return {"type": type_name, "params": params}


def _content_filter(profile: ServerProfile, filter_name: str,
                    filter_params: dict) -> Dict[str, Any]:
    filt = _wrapped(filter_name, filter_params)
    if profile.capabilities.get("generator_style_filter", True):
        return {
            "type": "DefaultMarkdownGenerator",
            "params": {"content_filter": filt, "options": {"body_width": 0}},
        }
    # 0.6.x style: top-level filter + fit flag (handled by caller merging this)
    return filt


def build_crawler_config(
    profile: ServerProfile,
    *,
    fit: bool = True,
    query: Optional[str] = None,
    content_type: Optional[str] = None,
    cache_mode: str = "bypass",
    css_selector: Optional[str] = None,
    excluded_selector: Optional[str] = None,
    target_elements: Optional[List[str]] = None,
    excluded_tags: Optional[List[str]] = None,
    word_count_threshold: Optional[int] = None,
    pruning: Optional[dict] = None,
    use_bm25: bool = False,
    bm25_threshold: float = 1.0,
    wait_for: Optional[str] = None,
    page_timeout: int = 30000,
    js_code: Optional[List[str]] = None,
    check_robots_txt: bool = True,
    verbose: bool = False,
    extra: Optional[dict] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Build the /crawl ``crawler_config`` dict for this server build.

    Returns (config, warnings). ``warnings`` carries version-gate messages
    (stripped fields) the caller should surface to the agent.

    RAG defaults (from Crawl4AI docs + practitioner presets):
    word_count_threshold ~10-20 (default 200 is far too high for docs),
    excluded_selector stripping nav/footer/aside chrome, dynamic-threshold
    PruningContentFilter, no line wrapping (body_width=0).
    """
    warnings: List[str] = []
    preset = rag_preset(content_type or "generic")
    caps = profile.capabilities

    cfg: Dict[str, Any] = {
        "cache_mode": _wrapped("CacheMode", cache_mode),
        "word_count_threshold": word_count_threshold
        or int(preset.get("word_count_threshold", 15)),
        "check_robots_txt": check_robots_txt,
        "verbose": verbose,
        "stream": False,
    }

    # ---- page timeout (0.9 clamps at 60000 ms server-side)
    page_timeout = max(1000, int(page_timeout))
    if caps.get("untrusted_gate") and page_timeout > 60000:
        warnings.append(
            f"page_timeout {page_timeout} clamped to 60000 (server >= 0.9 limit)"
        )
        page_timeout = 60000
    cfg["page_timeout"] = page_timeout

    # ---- DOM scoping
    if css_selector:
        cfg["css_selector"] = css_selector
    exc_selector = excluded_selector
    if exc_selector is None:
        exc_selector = preset.get("excluded_selector")
    if exc_selector:
        cfg["excluded_selector"] = exc_selector
    if excluded_tags:
        cfg["excluded_tags"] = list(excluded_tags)
    if target_elements:
        if caps.get("target_elements", True):
            cfg["target_elements"] = list(target_elements)
        else:
            warnings.append(
                "target_elements ignored (server < 0.8); css_selector/excluded_selector still apply"
            )

    # ---- content filter / markdown generation
    effective_preset = dict(preset.get("pruning") or {})
    if pruning:
        effective_preset.update(pruning)
    if use_bm25 and query:
        filt = _content_filter(
            profile, "BM25ContentFilter",
            {"user_query": query, "bm25_threshold": bm25_threshold, "use_stemming": True},
        )
    else:
        filt = _content_filter(profile, "PruningContentFilter", dict(effective_preset))

    if fit:
        if caps.get("generator_style_filter", True):
            cfg["markdown_generator"] = filt
        else:
            # 0.6.x: top-level content_filter object + fit flag
            cfg["content_filter"] = filt
            cfg["fit_markdown"] = True

    # ---- interaction knobs
    if wait_for:
        cfg["wait_for"] = wait_for
    if js_code:
        if caps.get("untrusted_gate"):
            warnings.append(
                "js_code stripped (REST forbidden on server >= 0.9); "
                "pages requiring JS interaction cannot be served via /crawl — "
                "use mode='fast' only if static rendering suffices, or downgrade server"
            )
        else:
            cfg["js_code"] = list(js_code)

    # ---- extra: last-wins merge, then 0.9 gate sweep
    if extra:
        cfg.update(extra)
    if caps.get("untrusted_gate"):
        banned = {
            "js_code", "session_id", "deep_crawl_strategy", "proxy_config",
            "magic", "base_url", "simulate_user", "override_navigator",
            "cookies", "headers", "user_agent",
        }
        banned_prefixes = ("LLM", "Proxy", "DeepCrawl")
        for key in list(cfg.keys()):
            if key in banned or any(key.startswith(p) for p in banned_prefixes):
                if key in cfg and key not in ("user_agent", "headers", "cookies"):
                    cfg.pop(key, None)
                    warnings.append(f"field '{key}' stripped (forbidden on server >= 0.9)")
        if cfg.get("content_filter") and isinstance(cfg["content_filter"], dict):
            pass
        # nested sweep: any {"type": "LLM..."} params
        for key, val in list(cfg.items()):
            if isinstance(val, dict) and str(val.get("type", "")).startswith(banned_prefixes):
                cfg.pop(key, None)
                warnings.append(f"strategy '{val.get('type')}' stripped (forbidden on server >= 0.9)")

    return cfg, warnings


def build_browser_config(
    profile: ServerProfile,
    *,
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    user_agent: Optional[str] = None,
    extra: Optional[dict] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Build the /crawl ``browser_config`` dict (version-gated, minimal)."""
    warnings: List[str] = []
    cfg: Dict[str, Any] = {
        "headless": headless,
        "viewport": {"width": min(viewport_width, 4000), "height": min(viewport_height, 4000)},
    }
    if user_agent:
        if profile.capabilities.get("untrusted_gate"):
            warnings.append("user_agent stripped (forbidden on server >= 0.9)")
        else:
            cfg["user_agent"] = user_agent
    if extra:
        cfg.update(extra)
        if profile.capabilities.get("untrusted_gate"):
            for key in ("proxy_config", "cookies", "headers"):
                if key in cfg:
                    cfg.pop(key, None)
                    warnings.append(f"browser field '{key}' stripped (forbidden on server >= 0.9)")
    return cfg, warnings


# --------------------------------------------------------------------------
# URL scoping + slugs

def slugify_url(url: str, max_len: int = 120) -> str:
    """Host + path -> filesystem-safe slug, e.g.
    https://docs.example.com/api/auth?x=1 -> docs.example.com/api/auth."""
    s = urlsplit(url)
    host = (s.netloc or "unknown").lower().replace(":", "-")
    path = s.path.strip("/")
    query = s.query[:40]
    slug = "/".join(
        re.sub(r"[^A-Za-z0-9._-]+", "-", seg).strip("-") for seg in path.split("/") if seg
    )
    full = f"{host}/{slug}" if slug else host
    if query:
        full += f"-{re.sub(r'[^A-Za-z0-9._-]+', '-', query).strip('-')}"
    if len(full) > max_len:
        full = full[:max_len].rstrip("-")
    return full


def url_allowed(
    url: str,
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
    base_host: Optional[str] = None,
    include_external: bool = False,
) -> bool:
    """Glob include/exclude on the full URL + optional same-host policy."""
    if include_external is False and base_host:
        host = urlsplit(url).netloc
        if host and host != base_host:
            return False
    inc = list(include or [])
    exc = list(exclude or [])
    if inc and not any(fnmatch(url, pat) or url.startswith(pat) for pat in inc):
        return False
    if any(fnmatch(url, pat) or url.startswith(pat) for pat in exc):
        return False
    return True


def normalize_url(href: str, base: Optional[str] = None) -> Optional[str]:
    """Absolute-ize, drop fragments, skip non-http schemes."""
    href = (href or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return None
    if href.startswith("//"):
        href = "https:" + href
    if base and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href):
        from urllib.parse import urljoin

        href = urljoin(base, href)
    if not href.startswith(("http://", "https://")):
        return None
    s = urlsplit(href)
    return urlunsplit((s.scheme, s.netloc, s.path, s.query, ""))


# --------------------------------------------------------------------------
# discovery: sitemaps + llms.txt

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _maybe_decompress(content: bytes) -> bytes:
    if content[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(content)
        except OSError:
            pass
    try:
        return zlib.decompress(content)
    except zlib.error:
        return content


async def expand_sitemap(
    client: Crawl4AIClient,
    sitemap_url: str,
    limit: int = 200,
    _depth: int = 0,
) -> List[str]:
    """Recursively expand sitemap / sitemap index files (gzip tolerated)."""
    if _depth > 3:
        return []
    try:
        raw = _maybe_decompress(await client.get_bytes(sitemap_url))
    except Exception:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    urls: List[str] = []

    def _tag(el: ET.Element) -> str:
        return el.tag.split("}", 1)[-1]

    if _tag(root) == "sitemapindex":
        for sm in root:
            if _tag(sm) == "sitemap":
                loc = next((c.text for c in sm if _tag(c) == "loc"), None)
                if loc and len(urls) < limit:
                    found = await expand_sitemap(client, loc.strip(), limit - len(urls),
                                                 _depth + 1)
                    urls.extend(found)
    else:
        for u in root:
            if _tag(u) == "url":
                loc = next((c.text for c in u if _tag(c) == "loc"), None)
                if loc:
                    urls.append(loc.strip())
    return urls[:limit]


def parse_llms_txt(text: str, limit: int = 500) -> List[str]:
    """Extract URLs from llms.txt / llms-full.txt markdown-ish content."""
    urls: List[str] = []
    seen = set()
    for m in re.finditer(r"https?://[^\s)\]>]+", text):
        u = normalize_url(m.group(0))
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
            if len(urls) >= limit:
                break
    return urls


async def discover_urls(
    client: Crawl4AIClient,
    sources: List[str],
    *,
    max_pages: int = 200,
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
) -> List[str]:
    """Resolve mixed sources (URL | sitemap.xml | llms.txt | file of URLs) to
    a de-duplicated, scope-filtered URL list."""
    from pathlib import Path

    urls: List[str] = []
    seen: set = set()
    for src in sources:
        sp = urlsplit(src)
        if src.endswith((".xml",)) or "sitemap" in sp.path:
            found = await expand_sitemap(client, src, limit=max_pages)
        elif src.endswith(("llms.txt", "llms-full.txt")):
            from .client import Crawl4AIError  # noqa: F401  (docs)

            text = (await client.get_bytes(src)).decode("utf-8", "replace")
            found = parse_llms_txt(text)
        elif src.startswith(("http://", "https://")):
            found = [src]
        elif Path(src).exists():
            found = [
                ln.strip()
                for ln in Path(src).read_text(encoding="utf-8").splitlines()
                if ln.strip().startswith("http")
            ]
        else:
            found = [src]
        for u in found:
            nu = normalize_url(u)
            if not nu or nu in seen:
                continue
            if not url_allowed(nu, include, exclude):
                continue
            seen.add(nu)
            urls.append(nu)
            if len(urls) >= max_pages and not src.endswith((".xml",)):
                break
        if len(urls) >= max_pages:
            break
    return urls[:max_pages]
