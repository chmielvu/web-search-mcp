from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from ..sanitize import sanitize_markdown

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


class GitHubPullError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubPullTarget:
    owner: str
    repo: str
    number: int


_PULL_RE = re.compile(r"^/([^/]+)/([^/]+)/pull/(\d+)(?:/|$)")


def parse_github_pull_url(url: str) -> GitHubPullTarget:
    """Parse a GitHub Pull Request URL: https://github.com/<owner>/<repo>/pull/<number>"""
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


def render_pull_markdown(
    *,
    pr: dict[str, Any],
    comments: list[dict[str, Any]],
) -> str:
    title = str(pr.get("title") or "").strip()
    url = str(pr.get("url") or "").strip()
    state = str(pr.get("state") or "").strip()
    created_at = str(pr.get("createdAt") or "").strip()
    author_login = ""
    author = pr.get("author")
    if isinstance(author, dict):
        author_login = str(author.get("login") or "").strip()

    additions = pr.get("additions", 0)
    deletions = pr.get("deletions", 0)
    changed_files = pr.get("changedFiles", 0)
    head_ref = pr.get("headRefName", "")
    base_ref = pr.get("baseRefName", "")

    body = sanitize_markdown(str(pr.get("body") or ""))

    lines: list[str] = [f"# Pull Request: {title}"]
    meta_parts = []
    if url:
        meta_parts.append(f"Link: {url}")
    if author_login:
        meta_parts.append(f"Author: @{author_login}")
    if created_at:
        meta_parts.append(f"Date: {created_at}")
    if state:
        meta_parts.append(f"State: {state}")
    if head_ref and base_ref:
        meta_parts.append(f"Branches: {head_ref} -> {base_ref}")
    meta_parts.append(f"Diff: +{additions} / -{deletions} ({changed_files} files)")

    lines.append(" | ".join(meta_parts))
    lines.append("")
    if body.strip():
        lines.append("## Description")
        lines.append(body.strip())
        lines.append("")

    if comments:
        lines.append("## Review Comments")
        for idx, c in enumerate(comments, start=1):
            c_author = ""
            auth_obj = c.get("author")
            if isinstance(auth_obj, dict):
                c_author = str(auth_obj.get("login") or "").strip()
            c_created = str(c.get("createdAt") or "").strip()
            c_body = sanitize_markdown(str(c.get("body") or ""))

            lines.append(f"### Comment {idx}")
            c_meta = []
            if c_author:
                c_meta.append(f"Author: @{c_author}")
            if c_created:
                c_meta.append(f"Date: {c_created}")
            lines.append(" | ".join(c_meta))
            lines.append("")
            lines.append(c_body.strip())
            lines.append("")

    return "\n".join(lines).strip() + "\n"


async def fetch_github_pull_thread_markdown(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
    max_comments: int = 50,
) -> str:
    target = parse_github_pull_url(url)
    token = os.environ.get("GITHUB_TOKEN", "").strip()

    async def _run(client: httpx.AsyncClient) -> str:
        headers = {"User-Agent": "kindly-web-search/1.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # GraphQL strategy
        if token:
            try:
                gql_query = """
                query ($owner: String!, $name: String!, $number: Int!) {
                  repository(owner: $owner, name: $name) {
                    pullRequest(number: $number) {
                      title
                      body
                      state
                      createdAt
                      url
                      additions
                      deletions
                      changedFiles
                      baseRefName
                      headRefName
                      author { login }
                      comments(first: 50) {
                        nodes {
                          body
                          createdAt
                          author { login }
                        }
                      }
                    }
                  }
                }
                """
                gql_resp = await client.post(
                    GITHUB_GRAPHQL_URL,
                    json={
                        "query": gql_query,
                        "variables": {
                            "owner": target.owner,
                            "name": target.repo,
                            "number": target.number,
                        },
                    },
                    headers=headers,
                )
                if gql_resp.status_code == 200:
                    data = gql_resp.json()
                    pr_data = data.get("data", {}).get("repository", {}).get("pullRequest")
                    if pr_data:
                        comments_nodes = (pr_data.get("comments") or {}).get("nodes", [])
                        return render_pull_markdown(
                            pr=pr_data, comments=comments_nodes[:max_comments]
                        )
            except Exception:
                pass  # Fallback to REST

        # REST API Fallback
        pr_url = f"https://api.github.com/repos/{target.owner}/{target.repo}/pulls/{target.number}"
        resp = await client.get(pr_url, headers=headers)
        if resp.status_code != 200:
            raise GitHubPullError(f"Pull Request not found or private (HTTP {resp.status_code}).")

        pr_info = resp.json()
        pr_data = {
            "title": pr_info.get("title"),
            "body": pr_info.get("body"),
            "state": pr_info.get("state", "").upper(),
            "createdAt": pr_info.get("created_at"),
            "url": pr_info.get("html_url"),
            "additions": pr_info.get("additions", 0),
            "deletions": pr_info.get("deletions", 0),
            "changedFiles": pr_info.get("changed_files", 0),
            "baseRefName": (pr_info.get("base") or {}).get("ref"),
            "headRefName": (pr_info.get("head") or {}).get("ref"),
            "author": {"login": (pr_info.get("user") or {}).get("login")},
        }

        comments: list[dict[str, Any]] = []
        comments_url = f"https://api.github.com/repos/{target.owner}/{target.repo}/issues/{target.number}/comments"
        comm_resp = await client.get(comments_url, headers=headers)
        if comm_resp.status_code == 200:
            for c in comm_resp.json():
                comments.append(
                    {
                        "body": c.get("body"),
                        "createdAt": c.get("created_at"),
                        "author": {"login": (c.get("user") or {}).get("login")},
                    }
                )

        return render_pull_markdown(pr=pr_data, comments=comments[:max_comments])

    if http_client is None:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            return await _run(client)
    return await _run(http_client)
