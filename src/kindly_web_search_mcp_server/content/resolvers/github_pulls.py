"""GitHub Pull Request thread producer returning RawDocument candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ..github_api import graphql_paginate_comments, resolve_github_token, thread_values

from ..models import (
    AcquisitionError,
    Diagnostic,
    FetchContext,
    ParsedURL,
    RawDocument,
    ResolverTarget,
    ThreadMessage,
)
from ..documents import build_thread_document


class GitHubPullError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubPullTarget:
    owner: str
    repo: str
    number: int


_PULL_RE = re.compile(r"^/([^/]+)/([^/]+)/pull/(\d+)(?:/|$)")


def parse_github_pull_url(url: str) -> GitHubPullTarget:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {"github.com", "www.github.com"}:
        raise GitHubPullError(f"Unsupported GitHub host: {host or '(missing)'}")
    path = parsed.path or ""
    m = _PULL_RE.match(path)
    if not m:
        raise GitHubPullError("URL is not a recognized GitHub Pull Request URL.")
    owner, repo, num = m.group(1), m.group(2), m.group(3)
    try:
        number = int(num)
    except Exception as exc:
        raise GitHubPullError("Invalid pull request number.") from exc
    return GitHubPullTarget(owner=owner, repo=repo, number=number)


def match_github_pull(parsed: ParsedURL) -> ResolverTarget | None:
    """Return a :class:`ResolverTarget` for any GitHub PR URL; else ``None``."""

    try:
        target = parse_github_pull_url(parsed.url)
    except GitHubPullError:
        return None
    return ResolverTarget(
        url=parsed.url,
        kind="github_pull",
        values={"owner": target.owner, "repo": target.repo, "number": str(target.number)},
    )


# ---------------------------------------------------------------------------
# New path — RawDocument via shared client + threads renderer.
# ---------------------------------------------------------------------------


_RAW_PULL_QUERY = """
query ($owner: String!, $name: String!, $number: Int!, $commentsCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      title url state createdAt body
      author { login }
      comments(first: 50, after: $commentsCursor) {
        pageInfo { hasNextPage endCursor }
        totalCount
        nodes { id author { login } body createdAt url }
      }
    }
  }
}
"""


def _pull_thread_messages(
    pr: dict[str, Any], comments: list[dict[str, Any]]
) -> tuple[ThreadMessage, ...]:
    out: list[ThreadMessage] = []
    pr_id = str(pr.get("id") or "pr_root")
    out.append(
        ThreadMessage(
            id=pr_id,
            role="pull_request",
            body=str(pr.get("body") or ""),
            body_format="markdown",
            author=(
                str(pr.get("author", {}).get("login"))
                if isinstance(pr.get("author"), dict) and pr.get("author", {}).get("login")
                else None
            ),
            created_at=str(pr.get("createdAt") or "") or None,
            accepted=False,
            permalink=str(pr.get("url") or "") or None,
            parent_id=None,
        )
    )
    for c in comments:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "")
        if not cid:
            continue
        body_text = str(c.get("body") or "")
        if not body_text.strip():
            continue
        author = None
        if isinstance(c.get("author"), dict):
            author = str(c["author"].get("login") or "") or None
        out.append(
            ThreadMessage(
                id=cid,
                role="comment",
                body=body_text,
                body_format="markdown",
                author=author,
                created_at=str(c.get("createdAt") or "") or None,
                permalink=str(c.get("url") or "") or None,
                parent_id=pr_id,
            )
        )
    return tuple(out)


async def fetch_github_pull_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Acquire a GitHub PR thread and return a :class:`RawDocument`."""

    owner, repo, number = thread_values(target)
    token = resolve_github_token()
    if not token:
        raise AcquisitionError(
            code="unauthorized",
            message="GITHUB_TOKEN not configured for GitHub pull request fetch",
            status="blocked",
            retryable=False,
        )

    pr_payload, comments, total = await graphql_paginate_comments(
        ctx,
        query=_RAW_PULL_QUERY,
        token=token,
        variables={"owner": owner, "name": repo, "number": number},
        resource="pullRequest",
        resource_label="pull request",
        cursor_variable="commentsCursor",
    )

    messages = _pull_thread_messages(pr_payload, comments)
    title = str(pr_payload.get("title") or f"PR {number}").strip()
    thread = build_thread_document(
        title=title,
        url=target.url,
        messages=messages,
        metadata={
            "kind": "github_pull",
            "owner": owner,
            "repo": repo,
            "number": number,
            "state": str(pr_payload.get("state") or "") or None,
            "total_comments": total,
        },
    )
    diagnostics = (
        Diagnostic(
            phase="acquisition",
            code="github_pull_fetched",
            message=f"GitHub {owner}/{repo}#{number} ({len(messages)} messages)",
        ),
    )
    return RawDocument(
        input_url=target.url,
        fetched_url=target.url,
        source_type="github_pull",
        fetch_backend="github_graphql",
        body=thread,
        title=title,
        metadata={
            "owner": owner,
            "repo": repo,
            "number": number,
            "state": thread.metadata.get("state"),
            "comment_count": total,
        },
        diagnostics=diagnostics,
        complete=bool(messages),
        scope="full",
    )


__all__ = [
    "GitHubPullError",
    "GitHubPullTarget",
    "parse_github_pull_url",
    "match_github_pull",
    "fetch_github_pull_raw",
]
