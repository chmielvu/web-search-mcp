"""Bounded multi-page Crawl4AI acquisition and shared artifact finalization."""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from kindly_web_search_mcp_server.models import CrawlWebRequest
from kindly_web_search_mcp_server.utils.url_canonicalize import (
    canonicalize_url,
    extract_domain_from_url,
)

from .constructor import ContentArtifact, finalize_artifact
from .fetch_pipeline import (
    evaluate_candidate,
    fetch_content_artifact,
    raw_document_from_crawl_item,
    select_candidate,
)
from .http_utils import SafeFetchError, validate_public_url
from .models import Candidate, ContentError, Diagnostic, FetchOptions
from .remote_clients import (
    Crawl4AIClientError,
    Crawl4AIConfigError,
    crawl4ai_browser_params,
    crawl4ai_markdown_params,
    extract_crawl_markdown_candidates,
    get_crawl4ai_client,
)

LOGGER = logging.getLogger(__name__)

_MAX_LINKS_PER_PAGE = 100


@dataclass(frozen=True, slots=True)
class CrawlArtifact:
    """One finalized page artifact and its traversal depth."""

    artifact: ContentArtifact
    depth: int


async def crawl_content_artifacts(request: CrawlWebRequest) -> list[CrawlArtifact]:
    """Traverse validated pages breadth-first and finalize every page artifact."""
    client = get_crawl4ai_client()
    if client is None:
        raise Crawl4AIConfigError(
            "Crawl4AI is not configured. Set CRAWL4AI_BASE_URL to the trusted "
            "Crawl4AI service endpoint and retry.",
            retryable=False,
        )

    seed_urls, seed_failures = await _validate_seed_urls(request)
    browser_params = crawl4ai_browser_params()
    crawler_params = _crawler_params(request)
    options = FetchOptions(processing_mode="index")
    visited = set(seed_urls)
    frontier: list[tuple[str, int]] = [(url, 0) for url in seed_urls]
    seed_hosts = {_url_host(url) for url in seed_urls}
    outcomes: list[CrawlArtifact] = []
    for url, error in seed_failures:
        artifact = await _finalize_failure(url, error, options=options)
        outcomes.append(CrawlArtifact(artifact=artifact, depth=0))

    while frontier and len(outcomes) < request.max_pages:
        remaining = request.max_pages - len(outcomes)
        batch = frontier[:remaining]
        del frontier[:remaining]
        batch_urls = [url for url, _ in batch]
        try:
            items = await client.crawl(
                batch_urls,
                browser_params=browser_params,
                crawler_params=crawler_params,
            )
        except Crawl4AIClientError as exc:
            for url, depth in batch:
                error = ContentError(
                    code="crawl4ai_batch_failed",
                    message=str(exc),
                    retryable=exc.retryable,
                    status="error",
                )
                artifact = await _finalize_failure(url, error, options=options)
                artifact = await _fetch_fallback_artifact(url, artifact, options=options)
                outcomes.append(CrawlArtifact(artifact=artifact, depth=depth))
            break

        matched_items = _match_items(batch_urls, items)
        for index, (url, depth) in enumerate(batch):
            item = matched_items[index]
            links = await _normalise_item_links(item, base_url=url)
            artifact = await _finalize_page(
                url,
                item,
                links=links,
                options=options,
            )
            artifact = await _fetch_fallback_artifact(url, artifact, options=options)
            outcomes.append(CrawlArtifact(artifact=artifact, depth=depth))
            if len(outcomes) >= request.max_pages or depth >= request.max_depth:
                continue
            for link in links:
                child_url_value = link.get("url")
                if not isinstance(child_url_value, str):
                    continue
                child_url = child_url_value
                if child_url in visited:
                    continue
                if not _url_allowed(
                    child_url,
                    request,
                    seed_hosts=seed_hosts,
                ):
                    continue
                visited.add(child_url)
                frontier.append((child_url, depth + 1))
    return outcomes


def _crawler_params(request: CrawlWebRequest) -> dict[str, object]:
    """Build the bounded CrawlerRunConfig parameter mapping.

    Delegates the Markdown generator and pruning filter to
    :func:`crawl4ai_markdown_params`. ``target_elements`` is omitted unless
    the request names a CSS target — sending ``article`` on a docs shell
    yields empty fit Markdown.
    """
    css_selector = None
    if request.targets is not None and request.targets.css_selector:
        css_selector = request.targets.css_selector
    interaction = request.interaction
    return crawl4ai_markdown_params(
        css_selector=css_selector,
        wait_for=None if interaction is None else interaction.wait_for,
        javascript=(
            None
            if interaction is None or not interaction.javascript
            else list(interaction.javascript)
        ),
        javascript_before_wait=(
            None
            if interaction is None or not interaction.javascript_before_wait
            else list(interaction.javascript_before_wait)
        ),
        scan_full_page=None if interaction is None else interaction.scan_full_page,
    )


async def _validate_seed_urls(
    request: CrawlWebRequest,
) -> tuple[list[str], list[tuple[str, ContentError]]]:
    """Canonicalize, SSRF-check, and constraint-check all seed URLs.

    Per-seed validation failures are returned as typed errors instead of
    aborting the request; only a seed outside the requested crawl targets
    rejects the whole request.
    """
    seeds: list[str] = []
    seen: set[str] = set()
    failures: list[tuple[str, ContentError]] = []
    for raw_url in request.urls:
        normalized = canonicalize_url(raw_url, fold_slug=False)
        try:
            await validate_public_url(normalized)
        except SafeFetchError as exc:
            failures.append(
                (
                    normalized,
                    ContentError(
                        code=exc.code,
                        message=str(exc),
                        retryable=exc.code == "dns_resolution_failed",
                        status="error",
                    ),
                )
            )
            continue
        if normalized in seen:
            continue
        if not _url_allowed(normalized, request, seed_hosts=set(), is_seed=True):
            raise ValueError(f"Seed URL is outside the requested crawl targets: {raw_url}")
        seen.add(normalized)
        seeds.append(normalized)
    return seeds, failures


def _url_allowed(
    url: str,
    request: CrawlWebRequest,
    *,
    seed_hosts: set[str],
    is_seed: bool = False,
) -> bool:
    """Apply domain and glob boundaries to one canonical URL."""
    host = _url_host(url)
    targets = request.targets
    if not host:
        return False
    if targets is not None:
        excluded_domains = _normalise_domains(targets.excluded_domains)
        if any(_domain_matches(host, domain) for domain in excluded_domains):
            return False
        allowed_domains = _normalise_domains(targets.allowed_domains)
        if allowed_domains and not any(_domain_matches(host, domain) for domain in allowed_domains):
            return False
        if targets.include_patterns and not any(
            fnmatch.fnmatchcase(url, pattern) for pattern in targets.include_patterns
        ):
            return False
        if targets.exclude_patterns and any(
            fnmatch.fnmatchcase(url, pattern) for pattern in targets.exclude_patterns
        ):
            return False
    if is_seed or request.include_external:
        return True
    return any(_same_site(host, seed_host) for seed_host in seed_hosts)


def _normalise_domains(values: list[str] | None) -> tuple[str, ...]:
    """Normalize domain values supplied as hosts or URLs."""
    domains: list[str] = []
    for value in values or ():
        domain = extract_domain_from_url(value) or value.strip().lower().removeprefix("www.")
        if domain:
            domains.append(domain)
    return tuple(dict.fromkeys(domains))


def _url_host(url: str) -> str:
    """Return a normalized hostname for a canonical URL."""
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _domain_matches(host: str, domain: str) -> bool:
    """Match a domain and all of its subdomains."""
    return host == domain or host.endswith(f".{domain}")


def _same_site(left: str, right: str) -> bool:
    """Treat a host and its parent/subdomain as one crawl site."""
    return left == right or left.endswith(f".{right}") or right.endswith(f".{left}")


async def _normalise_item_links(
    item: Mapping[str, object],
    *,
    base_url: str,
) -> tuple[dict[str, object], ...]:
    """Normalize public links from one Crawl4AI result and discard unsafe links."""
    links: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_value in _iter_link_values(item):
        href, text = raw_value
        absolute = urljoin(base_url, href.strip())
        parsed = urlsplit(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        normalized = canonicalize_url(absolute, fold_slug=False)
        if normalized in seen:
            continue
        try:
            await validate_public_url(normalized)
        except SafeFetchError:
            continue
        seen.add(normalized)
        domain = extract_domain_from_url(normalized)
        links.append(
            {
                "url": normalized,
                "text": text,
                "domain": domain,
                "internal": _same_site(_url_host(normalized), _url_host(base_url)),
            }
        )
        if len(links) >= _MAX_LINKS_PER_PAGE:
            break
    return tuple(links)


def _iter_link_values(item: Mapping[str, object]) -> Iterable[tuple[str, str]]:
    """Yield href/text pairs from Crawl4AI's flat or grouped link shapes."""
    for key in ("links", "internal_links", "external_links"):
        yield from _iter_link_value(item.get(key))


def _iter_link_value(value: object) -> Iterable[tuple[str, str]]:
    """Recursively flatten a Crawl4AI link value."""
    if isinstance(value, str):
        yield value, ""
        return
    if isinstance(value, Mapping):
        direct = value.get("href") or value.get("url") or value.get("uri")
        if isinstance(direct, str):
            label = value.get("text") or value.get("label") or value.get("title") or ""
            yield direct, str(label)
            return
        for nested in value.values():
            yield from _iter_link_value(nested)
        return
    if isinstance(value, (list, tuple)):
        for nested in value:
            yield from _iter_link_value(nested)


def _match_items(
    batch_urls: list[str],
    items: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Match response items to request order, falling back to positional order.

    Matching keys stay in request space (``fold_slug=False``): the slug fold
    is a dedup identity, so a folded key never equals the URL that was sent.
    Without that, every date-path URL silently missed the lookup — and
    because Crawl4AI returns batch results in completion order, the
    positional fallback could pair one page with another page's result.
    """
    matched: list[dict[str, object]] = [{} for _ in batch_urls]
    by_url: dict[str, list[int]] = {}
    for response_index, item in enumerate(items):
        raw_url = item.get("url") or item.get("source_url") or item.get("requested_url")
        if isinstance(raw_url, str):
            by_url.setdefault(canonicalize_url(raw_url, fold_slug=False), []).append(response_index)
    used: set[int] = set()
    for index, batch_url in enumerate(batch_urls):
        response_index = next(
            (candidate for candidate in by_url.get(batch_url, ()) if candidate not in used),
            None,
        )
        if response_index is None:
            response_index = next(
                (candidate for candidate in range(len(items)) if candidate not in used), None
            )
        if response_index is not None:
            used.add(response_index)
            matched[index] = items[response_index]
    return matched


async def _finalize_page(
    url: str,
    item: Mapping[str, object],
    *,
    links: tuple[dict[str, object], ...],
    options: FetchOptions,
) -> ContentArtifact:
    """Evaluate fit/raw variants, select the best candidate, and finalize once."""
    if item.get("success") is False:
        return await _finalize_failure(url, _item_error(item), options=options)

    candidates: list[Candidate] = []
    candidate_errors: list[str] = []
    for attempt_index, entry in enumerate(extract_crawl_markdown_candidates(item)):
        variant = entry["variant"]
        markdown = entry["markdown"]
        document = raw_document_from_crawl_item(
            url, dict(item), markdown, variant=variant, links=tuple(links)
        )
        try:
            candidate = await evaluate_candidate(
                document,
                attempt_index=attempt_index,
                options=options,
            )
        except Exception as exc:
            candidate_errors.append(type(exc).__name__)
            continue
        candidates.append(candidate)

    selected = select_candidate(candidates)
    if selected is None:
        code = "crawl4ai_candidate_failed" if candidate_errors else "empty_content"
        message = (
            "Crawl4AI Markdown could not be evaluated: " + ", ".join(candidate_errors)
            if candidate_errors
            else "Crawl4AI returned no usable Markdown."
        )
        return await _finalize_failure(
            url,
            ContentError(code=code, message=message, status="error"),
            options=options,
        )
    return await finalize_artifact(url, selected, options=options)


def _item_error(item: Mapping[str, object]) -> ContentError:
    """Convert a failed Crawl4AI result into a stable content error."""
    status_code = item.get("status_code") or item.get("status")
    http_status = (
        status_code if isinstance(status_code, int) and not isinstance(status_code, bool) else None
    )
    status = "blocked" if http_status in {401, 403, 429} else "error"
    message = item.get("error") or item.get("message") or "Crawl4AI failed to process this page."
    retryable = bool(item.get("retryable")) or bool(http_status and http_status >= 500)
    return ContentError(
        code=f"http_{http_status}" if http_status and http_status >= 400 else "crawl4ai_failed",
        message=str(message),
        retryable=retryable,
        http_status=http_status,
        status=status,
    )


def _needs_fetch_fallback(artifact: ContentArtifact) -> bool:
    """Return True when the Crawl4AI result carried no usable page."""
    return artifact.error is not None or not artifact.quality.accepted


async def _fetch_fallback_artifact(
    url: str,
    artifact: ContentArtifact,
    *,
    options: FetchOptions,
) -> ContentArtifact:
    """Retry one unacquired page through the single-URL ladder, skipping Crawl4AI.

    The bounded crawl already used ``POST /crawl``. A page it cannot acquire
    still gets one pass through Jina → Camoufox → Unlocker → archive.
    The original artifact survives whenever the fallback does not produce
    accepted content.
    """
    if not _needs_fetch_fallback(artifact):
        return artifact
    try:
        fallback = await fetch_content_artifact(
            url,
            fetch_options=options,
            skip_stages=frozenset({"crawl4ai_remote"}),
        )
    except TimeoutError as exc:
        LOGGER.warning("Fetch fallback failed for %s: %s", url, exc)
        return artifact
    if fallback.error is None and fallback.quality.accepted:
        LOGGER.info("Fetch fallback acquired %s after Crawl4AI returned no usable page", url)
        return fallback
    return artifact


async def _finalize_failure(
    url: str,
    error: ContentError,
    *,
    options: FetchOptions,
) -> ContentArtifact:
    """Finalize a failure through the same artifact constructor as success."""
    diagnostic = Diagnostic(
        code=error.code,
        message=error.message,
        severity="error",
        source="crawl4ai",
        phase="acquire",
    )
    return await finalize_artifact(
        url,
        None,
        failure=error,
        failure_status=error.status or "error",
        extra_diagnostics=(diagnostic,),
        options=options,
    )


__all__ = ["CrawlArtifact", "crawl_content_artifacts"]
