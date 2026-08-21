from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from ..sanitize import sanitize_markdown
from ...utils.github import normalize_github_repository

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


class GitHubRepoError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubRepoTarget:
    owner: str
    repo: str
    ref: str | None = None
    path: str | None = None


_EXCLUDED_SUBPATHS = {
    "issues",
    "pull",
    "pulls",
    "discussions",
    "releases",
    "actions",
    "settings",
    "commits",
    "commit",
    "wiki",
    "projects",
    "security",
    "insights",
}


def parse_github_repo_url(url: str) -> GitHubRepoTarget:
    """Parse a GitHub repository URL or SSH repository specification."""
    value = url.strip()
    if value.casefold().startswith("git@github.com:"):
        try:
            normalized = normalize_github_repository(value)
        except ValueError as exc:
            raise GitHubRepoError("URL is not a recognized GitHub repository URL.") from exc
        owner, repo = normalized.split("/", 1)
        return GitHubRepoTarget(owner=owner, repo=repo)

    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host not in {"github.com", "www.github.com"}:
        raise GitHubRepoError(f"Unsupported GitHub host: {host or '(missing)'}")

    path_parts = [p for p in (parsed.path or "").split("/") if p]
    if len(path_parts) < 2:
        raise GitHubRepoError("URL is not a recognized GitHub repository URL.")

    try:
        normalized = normalize_github_repository("/".join(path_parts[:2]))
    except ValueError as exc:
        raise GitHubRepoError("URL is not a recognized GitHub repository URL.") from exc
    owner, repo = normalized.split("/", 1)

    if len(path_parts) > 2:
        first_sub = path_parts[2].lower()
        if first_sub in _EXCLUDED_SUBPATHS:
            raise GitHubRepoError(f"URL points to a sub-resource ({first_sub}), not a repo root.")

        if first_sub == "tree" and len(path_parts) >= 4:
            ref = path_parts[3]
            subpath = "/".join(path_parts[4:]) if len(path_parts) > 4 else None
            return GitHubRepoTarget(owner=owner, repo=repo, ref=ref, path=subpath)

    return GitHubRepoTarget(owner=owner, repo=repo)


def render_repo_markdown(*, metadata: dict[str, Any], readme_text: str | None = None) -> str:
    owner = metadata.get("owner", "")
    repo = metadata.get("name", "")
    desc = str(metadata.get("description") or "").strip()
    stars = metadata.get("stargazerCount", 0)
    forks = metadata.get("forkCount", 0)
    lang = metadata.get("primaryLanguage")
    license_info = metadata.get("licenseInfo")
    branch = metadata.get("defaultBranch")
    topics = metadata.get("topics", [])

    lines: list[str] = [f"# Repository: {owner}/{repo}"]
    meta_parts = []
    if desc:
        meta_parts.append(f"Description: {desc}")
    meta_parts.append(f"Stars: {stars}")
    meta_parts.append(f"Forks: {forks}")
    if lang:
        meta_parts.append(f"Language: {lang}")
    if license_info:
        meta_parts.append(f"License: {license_info}")
    if branch:
        meta_parts.append(f"Default Branch: {branch}")
    if topics:
        meta_parts.append(f"Topics: {', '.join(topics)}")

    lines.append(" | ".join(meta_parts))
    lines.append("")

    if readme_text and readme_text.strip():
        lines.append("## README")
        lines.append(sanitize_markdown(readme_text).strip())
    else:
        lines.append("_No README found in repository._")

    return "\n".join(lines).strip() + "\n"


async def fetch_github_repo_markdown(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    target = parse_github_repo_url(url)
    token = os.environ.get("GITHUB_TOKEN", "").strip()

    async def _run(client: httpx.AsyncClient) -> str:
        headers = {"User-Agent": "kindly-web-search/1.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # Attempt GraphQL if token is provided
        if token:
            try:
                gql_query = """
                query ($owner: String!, $name: String!) {
                  repository(owner: $owner, name: $name) {
                    description
                    stargazerCount
                    forkCount
                    primaryLanguage { name }
                    licenseInfo { name spdxId }
                    defaultBranchRef { name }
                    repositoryTopics(first: 10) { nodes { topic { name } } }
                    readme: object(expression: "HEAD:README.md") { ... on Blob { text } }
                  }
                }
                """
                gql_resp = await client.post(
                    GITHUB_GRAPHQL_URL,
                    json={
                        "query": gql_query,
                        "variables": {"owner": target.owner, "name": target.repo},
                    },
                    headers=headers,
                )
                if gql_resp.status_code == 200:
                    data = gql_resp.json()
                    repo_data = data.get("data", {}).get("repository")
                    if repo_data:
                        lang_obj = repo_data.get("primaryLanguage") or {}
                        lic_obj = repo_data.get("licenseInfo") or {}
                        branch_obj = repo_data.get("defaultBranchRef") or {}
                        topics_nodes = (repo_data.get("repositoryTopics") or {}).get("nodes", [])
                        topics = [
                            t.get("topic", {}).get("name")
                            for t in topics_nodes
                            if t.get("topic", {}).get("name")
                        ]
                        readme_obj = repo_data.get("readme") or {}
                        readme_text = readme_obj.get("text")

                        meta = {
                            "owner": target.owner,
                            "name": target.repo,
                            "description": repo_data.get("description"),
                            "stargazerCount": repo_data.get("stargazerCount", 0),
                            "forkCount": repo_data.get("forkCount", 0),
                            "primaryLanguage": lang_obj.get("name"),
                            "licenseInfo": lic_obj.get("spdxId") or lic_obj.get("name"),
                            "defaultBranch": branch_obj.get("name"),
                            "topics": topics,
                        }
                        return render_repo_markdown(metadata=meta, readme_text=readme_text)
            except Exception:
                pass  # Fallback to REST

        # REST API Fallback
        repo_url = f"https://api.github.com/repos/{target.owner}/{target.repo}"
        resp = await client.get(repo_url, headers=headers)
        if resp.status_code != 200:
            raise GitHubRepoError(f"Repository not found or private (HTTP {resp.status_code}).")

        repo_info = resp.json()
        license_obj = repo_info.get("license") or {}
        meta = {
            "owner": target.owner,
            "name": target.repo,
            "description": repo_info.get("description"),
            "stargazerCount": repo_info.get("stargazers_count", 0),
            "forkCount": repo_info.get("forks_count", 0),
            "primaryLanguage": repo_info.get("language"),
            "licenseInfo": license_obj.get("spdx_id") or license_obj.get("name"),
            "defaultBranch": repo_info.get("default_branch"),
            "topics": repo_info.get("topics", []),
        }

        readme_text = None
        readme_url = f"https://api.github.com/repos/{target.owner}/{target.repo}/readme"
        raw_headers = {**headers, "Accept": "application/vnd.github.raw+json"}
        readme_resp = await client.get(readme_url, headers=raw_headers)
        if readme_resp.status_code == 200:
            readme_text = readme_resp.text

        return render_repo_markdown(metadata=meta, readme_text=readme_text)

    if http_client is None:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            return await _run(client)
    return await _run(http_client)
