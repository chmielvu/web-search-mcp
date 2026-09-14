"""GitHub repository producer returning RawDocument candidates."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from ...utils.github import normalize_github_repository
from ..github_api import (
    fetch_readme_markdown,
    github_graphql,
    repo_values,
    resolve_github_token,
    rest_get,
)
from ..models import (
    AcquisitionError,
    Diagnostic,
    FetchContext,
    ParsedURL,
    RawDocument,
    RepositoryDocument,
    ResolverTarget,
)
from ..documents import build_repository_document


class GitHubRepoError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubRepoTarget:
    owner: str
    repo: str
    ref: str | None = None
    path: str | None = None


_GH_HOSTS = frozenset({"github.com", "www.github.com"})


def parse_github_repo_url(url: str) -> GitHubRepoTarget:
    """Parse a GitHub repository URL or SSH repository specification."""

    value = url.strip()
    if value.casefold().startswith("git@github.com:"):
        suffix = value.split(":", 1)[1]
        if suffix.endswith(".git"):
            suffix = suffix[:-4]
        owner, _, repo = suffix.partition("/")
        return GitHubRepoTarget(owner=owner, repo=repo)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in _GH_HOSTS:
        raise GitHubRepoError(f"Unsupported GitHub host: {host or '(missing)'}")
    parts = [p for p in (parsed.path or "").strip("/").split("/") if p]
    if len(parts) < 2:
        raise GitHubRepoError("GitHub repo URL requires /<owner>/<repo>")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    ref: str | None = None
    path: str | None = None
    if len(parts) >= 4 and parts[2] == "tree":
        ref = parts[3]
        path = "/".join(parts[4:]) or None
    elif len(parts) >= 4 and parts[2] == "blob":
        ref = parts[3]
        path = "/".join(parts[4:]) or None
    try:
        normalized = normalize_github_repository(f"{owner}/{repo}")
        owner, repo = normalized.split("/", 1)
    except Exception:
        pass
    return GitHubRepoTarget(owner=owner, repo=repo, ref=ref, path=path)


def match_github_repo(parsed: ParsedURL) -> ResolverTarget | None:
    try:
        target = parse_github_repo_url(parsed.url)
    except GitHubRepoError:
        return None
    values: dict[str, str] = {"owner": target.owner, "repo": target.repo}
    if target.ref:
        values["ref"] = target.ref
    if target.path:
        values["path"] = target.path
    return ResolverTarget(url=parsed.url, kind="github_repo", values=values)


_REPO_GRAPHQL = """
query ($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    description
    owner { login }
    stargazers { totalCount }
    forks { totalCount }
    licenseInfo { name }
    updatedAt
    languages(first: 10) { nodes { name } }
  }
}
"""


async def fetch_github_repo_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    owner, repo, ref = repo_values(target)
    token = resolve_github_token()
    if not token:
        raise AcquisitionError(
            code="unauthorized",
            message="GITHUB_TOKEN not configured for GitHub repository fetch",
            status="blocked",
            retryable=False,
        )

    data = await github_graphql(
        ctx,
        query=_REPO_GRAPHQL,
        variables={"owner": owner, "name": repo},
        token=token,
    )
    repo_node = data.get("repository") if isinstance(data, dict) else None
    if not isinstance(repo_node, dict):
        raise AcquisitionError(
            code="graphql_data_missing", message=f"GitHub repo {owner}/{repo} missing"
        )

    readme_text = ""
    try:
        readme_text = await fetch_readme_markdown(ctx, owner=owner, repo=repo, token=token)
    except AcquisitionError:
        readme_text = ""

    files: tuple[str, ...] = ()
    try:
        tree = await rest_get(
            ctx,
            path=f"repos/{owner}/{repo}/contents",
            token=token,
            params={"ref": ref} if ref else None,
        )
        if isinstance(tree, list):
            names: list[str] = []
            for item in tree:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if isinstance(name, str) and name:
                    names.append(name)
            files = tuple(names)
    except AcquisitionError:
        files = ()

    metadata_payload = {
        "owner": owner,
        "name": repo,
        "description": repo_node.get("description"),
        "stargazers": (repo_node.get("stargazers") or {}).get("totalCount")
        if isinstance(repo_node.get("stargazers"), dict)
        else None,
        "forks": (repo_node.get("forks") or {}).get("totalCount")
        if isinstance(repo_node.get("forks"), dict)
        else None,
        "license": (repo_node.get("licenseInfo") or {}).get("name")
        if isinstance(repo_node.get("licenseInfo"), dict)
        else None,
        "updated_at": repo_node.get("updatedAt"),
        "languages": [
            node.get("name")
            for node in (repo_node.get("languages") or {}).get("nodes") or []
            if isinstance(node, dict) and isinstance(node.get("name"), str)
        ],
        "ref": ref,
    }

    repo_doc: RepositoryDocument = build_repository_document(
        name=f"{owner}/{repo}",
        url=target.url,
        readme_text=readme_text,
        readme_format="text/markdown",
        files=files,
        links=(),
        metadata={k: v for k, v in metadata_payload.items() if v is not None},
    )

    diagnostics = (
        Diagnostic(
            phase="acquisition",
            code="github_repo_fetched",
            message=f"GitHub {owner}/{repo}",
        ),
    )
    return RawDocument(
        input_url=target.url,
        fetched_url=target.url,
        source_type="github_repo",
        fetch_backend="github_graphql",
        body=repo_doc,
        title=f"{owner}/{repo}",
        metadata={
            "owner": owner,
            "repo": repo,
            "ref": ref,
            "stargazers": metadata_payload["stargazers"],
            "forks": metadata_payload["forks"],
        },
        diagnostics=diagnostics,
        complete=bool(readme_text or files),
        scope="full",
    )


__all__ = [
    "GitHubRepoError",
    "GitHubRepoTarget",
    "parse_github_repo_url",
    "match_github_repo",
    "fetch_github_repo_raw",
]
