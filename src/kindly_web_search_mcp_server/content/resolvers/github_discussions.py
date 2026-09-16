from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ..documents import build_thread_document
from ..github_api import graphql_paginate_comments, resolve_github_token, thread_values
from ..models import (
    AcquisitionError,
    Diagnostic,
    FetchContext,
    ParsedURL,
    RawDocument,
    ResolverTarget,
    ThreadDocument,
    ThreadMessage,
)

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


class GitHubDiscussionError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubDiscussionTarget:
    owner: str
    repo: str
    number: int


_DISCUSSION_RE = re.compile(r"^/([^/]+)/([^/]+)/discussions/(\d+)(?:/|$)")


def parse_github_discussion_url(url: str) -> GitHubDiscussionTarget:
    """
    Parse a GitHub discussion URL: https://github.com/<owner>/<repo>/discussions/<number>

    Ignores query params and fragments.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {"github.com", "www.github.com"}:
        raise GitHubDiscussionError(f"Unsupported GitHub host: {host or '(missing)'}")

    path = parsed.path or ""
    m = _DISCUSSION_RE.match(path)
    if not m:
        raise GitHubDiscussionError("URL is not a recognized GitHub Discussion URL.")

    owner, repo, num = m.group(1), m.group(2), m.group(3)
    try:
        number = int(num)
    except Exception as exc:
        raise GitHubDiscussionError("Invalid discussion number.") from exc

    return GitHubDiscussionTarget(owner=owner, repo=repo, number=number)


def match_github_discussion(parsed: ParsedURL) -> ResolverTarget | None:
    """Return a :class:`ResolverTarget` for any GitHub discussion URL; else ``None``."""

    try:
        target = parse_github_discussion_url(parsed.url)
    except GitHubDiscussionError:
        return None
    return ResolverTarget(
        url=parsed.url,
        kind="github_discussion",
        values={"owner": target.owner, "repo": target.repo, "number": str(target.number)},
    )


_RAW_DISCUSSION_QUERY = """
query ($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    discussion(number: $number) {
      id
      title
      url
      createdAt
      updatedAt
      body
      upvoteCount
      isAnswered
      activeLockReason
      answerChosenAt
      answerChosenBy { login }
      answer { id body author { login } createdAt url }
      author { login }
      category { name }
      comments(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        totalCount
        nodes {
          id
          author { login }
          body
          createdAt
          url
        }
      }
    }
  }
}
"""


def _discussion_thread_messages(
    discussion: dict[str, Any], comments: list[dict[str, Any]]
) -> tuple[ThreadMessage, ...]:
    out: list[ThreadMessage] = []
    root_id = str(discussion.get("id") or "discussion_root")
    out.append(
        ThreadMessage(
            id=root_id,
            role="discussion",
            body=str(discussion.get("body") or ""),
            body_format="markdown",
            author=(
                str(discussion.get("author", {}).get("login"))
                if isinstance(discussion.get("author"), dict)
                and discussion.get("author", {}).get("login")
                else None
            ),
            created_at=str(discussion.get("createdAt") or "") or None,
            score=discussion.get("upvoteCount")
            if isinstance(discussion.get("upvoteCount"), int)
            else None,
            accepted=bool(discussion.get("isAnswered")),
            permalink=str(discussion.get("url") or "") or None,
            parent_id=None,
        )
    )
    answer_id = ""
    answer = discussion.get("answer")
    if isinstance(answer, dict):
        answer_id = str(answer.get("id") or "")
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        cid = str(comment.get("id") or "")
        if not cid:
            continue
        body = str(comment.get("body") or "")
        if not body.strip():
            continue
        author = None
        if isinstance(comment.get("author"), dict):
            author = str(comment["author"].get("login") or "") or None
        is_answer = bool(answer_id) and cid == answer_id
        out.append(
            ThreadMessage(
                id=cid,
                role="answer" if is_answer else "comment",
                body=body,
                body_format="markdown",
                author=author,
                created_at=str(comment.get("createdAt") or "") or None,
                permalink=str(comment.get("url") or "") or None,
                parent_id=root_id,
            )
        )
    return tuple(out)


async def fetch_github_discussion_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Acquire a GitHub discussion thread and return a :class:`RawDocument`."""

    owner, repo, number = thread_values(target)
    token = resolve_github_token()
    if not token:
        raise AcquisitionError(
            code="unauthorized",
            message="GITHUB_TOKEN not configured for GitHub discussion fetch",
            status="blocked",
            retryable=False,
        )

    discussion_payload, comments, total = await graphql_paginate_comments(
        ctx,
        query=_RAW_DISCUSSION_QUERY,
        token=token,
        variables={"owner": owner, "name": repo, "number": number},
        resource="discussion",
        resource_label="discussion",
    )

    messages = _discussion_thread_messages(discussion_payload, comments)
    title = str(discussion_payload.get("title") or f"Discussion {number}").strip()
    thread: ThreadDocument = build_thread_document(
        title=title,
        url=target.url,
        messages=messages,
        metadata={
            "kind": "github_discussion",
            "owner": owner,
            "repo": repo,
            "number": number,
            "is_answered": bool(discussion_payload.get("isAnswered")),
            "total_comments": total,
        },
    )
    diagnostics = (
        Diagnostic(
            phase="acquisition",
            code="github_discussion_fetched",
            message=f"GitHub discussion {owner}/{repo}#{number} ({len(messages)} messages)",
        ),
    )
    return RawDocument(
        input_url=target.url,
        fetched_url=target.url,
        source_type="github_discussion",
        fetch_backend="github_graphql",
        body=thread,
        title=title,
        metadata={
            "owner": owner,
            "repo": repo,
            "number": number,
            "is_answered": thread.metadata.get("is_answered"),
            "comment_count": total,
        },
        diagnostics=diagnostics,
        complete=bool(messages),
        scope="full",
    )
