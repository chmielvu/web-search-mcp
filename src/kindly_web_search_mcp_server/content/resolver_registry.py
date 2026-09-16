"""Static ordered :class:`ResolverSpec` registry for the content pipeline.

The pipeline iterates :data:`REGISTRY` in priority order — first match wins.
Each spec owns its ``match_*`` + ``fetch_*_raw`` pair, defined in its resolver
module under :mod:`kindly_web_search_mcp_server.content.resolvers`; this file
is only the priority-ordered assembly. ``match`` is a pure
``(ParsedURL) -> ResolverTarget | None`` callable; ``fetch`` is an
``async (ResolverTarget, FetchContext) -> RawDocument`` callable that borrows
the client and deadline from ``FetchContext`` and raises
:class:`AcquisitionError` on any failure. ``None`` from a match lets the next
resolver claim the URL.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .models import (
    FetchContext,
    ParsedURL,
    RawDocument,
    ResolverSpec,
    ResolverTarget,
)
from .resolvers.arxiv import fetch_arxiv_raw, match_arxiv
from .resolvers.crates import fetch_crates_raw, match_crates
from .resolvers.discourse import fetch_discourse_raw, match_discourse
from .resolvers.files import fetch_document_raw, match_document
from .resolvers.github_discussions import fetch_github_discussion_raw, match_github_discussion
from .resolvers.github_issues import fetch_github_issue_raw, match_github_issue
from .resolvers.github_pulls import fetch_github_pull_raw, match_github_pull
from .resolvers.github_repo import fetch_github_repo_raw, match_github_repo
from .resolvers.hackernews import fetch_hackernews_raw, match_hackernews
from .resolvers.huggingface import fetch_huggingface_raw, match_huggingface
from .resolvers.llms_txt import fetch_llms_txt_raw, match_llms_txt
from .resolvers.md_twin import fetch_md_twin_raw, match_md_twin
from .resolvers.npm import fetch_npm_raw, match_npm
from .resolvers.pypi import fetch_pypi_raw, match_pypi
from .resolvers.raw_text import fetch_raw_text_raw, match_raw_text
from .resolvers.reddit import fetch_reddit_raw, match_reddit
from .resolvers.stackexchange import fetch_stackexchange_raw, match_stackexchange
from .resolvers.telegram import fetch_telegram_raw, match_telegram
from .resolvers.twitter import fetch_twitter_raw, match_twitter
from .resolvers.unpaywall import fetch_doi_raw, match_doi
from .resolvers.wayback import fetch_wayback_raw, match_wayback
from .resolvers.wikipedia import fetch_wikipedia_raw, match_wikipedia
from .resolvers.youtube import fetch_youtube_raw, match_youtube

MatchFn = Callable[[ParsedURL], ResolverTarget | None]
FetchFn = Callable[[ResolverTarget, FetchContext], Awaitable[RawDocument]]


def _build(
    name: str,
    *,
    match: MatchFn,
    fetch: FetchFn,
) -> ResolverSpec:
    """Wrap a (match, fetch) pair into a :class:`ResolverSpec`."""

    def _wrapped_match(parsed: ParsedURL) -> ResolverTarget | None:
        if not match:
            return None
        try:
            result = match(parsed)
        except Exception:
            return None
        if result is None:
            return None
        if not result.allow_generic:
            return ResolverTarget(
                url=result.url,
                kind=result.kind,
                values=result.values,
                allow_generic=False,
            )
        return result

    async def _wrapped_fetch(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
        return await fetch(target, ctx)

    return ResolverSpec(
        name=name,
        match=_wrapped_match,
        fetch=_wrapped_fetch,
    )


# ---------------------------------------------------------------------------
# Forum/thread producers (already migrated).
# ---------------------------------------------------------------------------


SPEC_PYPI = _build("pypi", match=match_pypi, fetch=fetch_pypi_raw)
SPEC_NPM = _build("npm", match=match_npm, fetch=fetch_npm_raw)
SPEC_HUGGINGFACE = _build("huggingface", match=match_huggingface, fetch=fetch_huggingface_raw)
SPEC_CRATES = _build("crates", match=match_crates, fetch=fetch_crates_raw)

SPEC_GITHUB_ISSUE = _build("github_issue", match=match_github_issue, fetch=fetch_github_issue_raw)
SPEC_GITHUB_PULL = _build("github_pull", match=match_github_pull, fetch=fetch_github_pull_raw)
SPEC_GITHUB_DISCUSSION = _build(
    "github_discussion", match=match_github_discussion, fetch=fetch_github_discussion_raw
)
SPEC_GITHUB_REPO = _build("github_repo", match=match_github_repo, fetch=fetch_github_repo_raw)

SPEC_STACKEXCHANGE = _build(
    "stackexchange", match=match_stackexchange, fetch=fetch_stackexchange_raw
)
SPEC_DISCOURSE = _build("discourse", match=match_discourse, fetch=fetch_discourse_raw)
SPEC_HACKERNEWS = _build("hackernews", match=match_hackernews, fetch=fetch_hackernews_raw)
SPEC_REDDIT = _build("reddit", match=match_reddit, fetch=fetch_reddit_raw)

# ---------------------------------------------------------------------------
# Stubs for resolvers whose migration is on the next slice.
# ---------------------------------------------------------------------------


# Priority-ordered registry.
# ---------------------------------------------------------------------------


SPEC_DOCUMENT = _build("document", match=match_document, fetch=fetch_document_raw)
SPEC_RAW_TEXT = _build("raw_text", match=match_raw_text, fetch=fetch_raw_text_raw)
SPEC_ARXIV = _build("arxiv", match=match_arxiv, fetch=fetch_arxiv_raw)
SPEC_DOI = _build("doi", match=match_doi, fetch=fetch_doi_raw)
SPEC_WIKIPEDIA = _build("wikipedia", match=match_wikipedia, fetch=fetch_wikipedia_raw)
SPEC_WAYBACK = _build("wayback", match=match_wayback, fetch=fetch_wayback_raw)
SPEC_TELEGRAM = _build("telegram", match=match_telegram, fetch=fetch_telegram_raw)
SPEC_TWITTER = _build("twitter", match=match_twitter, fetch=fetch_twitter_raw)
SPEC_YOUTUBE = _build("youtube", match=match_youtube, fetch=fetch_youtube_raw)
SPEC_LLMS_TXT = _build("llms_txt", match=match_llms_txt, fetch=fetch_llms_txt_raw)
SPEC_MD_TWIN = _build("md_twin", match=match_md_twin, fetch=fetch_md_twin_raw)


REGISTRY: tuple[ResolverSpec, ...] = (
    # 1. Explicit files / documents (PDF, Office, notebook, CSV, Google Docs).
    SPEC_DOCUMENT,
    # 2. Typed raw text: JSON/JSONL, YAML, TOML, feeds, subtitles, SVG, CSV, XML.
    SPEC_RAW_TEXT,
    # 3. Specific platform resources (threads / repos):
    SPEC_GITHUB_ISSUE,
    SPEC_GITHUB_PULL,
    SPEC_GITHUB_DISCUSSION,
    SPEC_STACKEXCHANGE,
    SPEC_DISCOURSE,
    SPEC_HACKERNEWS,
    SPEC_REDDIT,
    SPEC_TELEGRAM,
    SPEC_TWITTER,
    SPEC_YOUTUBE,
    SPEC_GITHUB_REPO,
    # 4. Repo / archive / academic:
    SPEC_HUGGINGFACE,
    SPEC_ARXIV,
    SPEC_DOI,
    SPEC_WIKIPEDIA,
    SPEC_WAYBACK,
    # 5. Package registries (PyPI, npm, HF, Crates — repository kept distinct above):
    SPEC_PYPI,
    SPEC_NPM,
    SPEC_CRATES,
    # 6. llms.txt root-only candidate; short-circuits only when nothing else claims.
    SPEC_LLMS_TXT,
    # 7. Markdown-twin probe (page.md sibling): cheap generic win before Jina.
    SPEC_MD_TWIN,
)


__all__ = ["REGISTRY", "FetchFn", "MatchFn", "_build"]
