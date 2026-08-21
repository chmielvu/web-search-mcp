from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.content.resolvers.github_repo import (
    GitHubRepoError,
    parse_github_repo_url,
)
from kindly_web_search_mcp_server.tools.code_search.exploration import _normalize_repository
from kindly_web_search_mcp_server.utils.github import normalize_github_repository


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("owner/repo", "owner/repo"),
        ("https://github.com/owner/repo", "owner/repo"),
        ("http://github.com/owner/repo.git", "owner/repo"),
        ("git@github.com:owner/repo", "owner/repo"),
        ("git@github.com:owner/repo.git", "owner/repo"),
    ],
)
def test_normalize_github_repository_accepts_common_specs(value: str, expected: str) -> None:
    assert normalize_github_repository(value) == expected
    assert _normalize_repository(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "owner",
        "owner/repo/extra",
        "https://gitlab.com/owner/repo",
        "git@github.com:owner",
    ],
)
def test_normalize_github_repository_rejects_invalid_specs(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_github_repository(value)


def test_content_parser_accepts_transport_variants_and_preserves_tree_path() -> None:
    for value in (
        "https://github.com/owner/repo.git",
        "http://github.com/owner/repo",
        "git@github.com:owner/repo.git",
    ):
        target = parse_github_repo_url(value)
        assert (target.owner, target.repo, target.ref, target.path) == (
            "owner",
            "repo",
            None,
            None,
        )

    target = parse_github_repo_url("https://github.com/owner/repo.git/tree/main/src")
    assert (target.owner, target.repo, target.ref, target.path) == (
        "owner",
        "repo",
        "main",
        "src",
    )


def test_content_parser_preserves_host_error_contract() -> None:
    with pytest.raises(GitHubRepoError, match="Unsupported GitHub host"):
        parse_github_repo_url("https://gitlab.com/owner/repo")
