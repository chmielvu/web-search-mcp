from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from kindly_web_search_mcp_server.content.resolvers.crates import (
    parse_crates_url,
    render_crates_markdown,
)
from kindly_web_search_mcp_server.content.resolvers.discourse import (
    parse_discourse_url,
    render_discourse_markdown,
)
from kindly_web_search_mcp_server.content.resolvers.huggingface import (
    parse_huggingface_url,
    render_huggingface_markdown,
)
from kindly_web_search_mcp_server.content.resolvers.npm import (
    parse_npm_url,
    render_npm_markdown,
)
from kindly_web_search_mcp_server.content.resolvers.pypi import (
    fetch_pypi_package_markdown,
    parse_pypi_url,
    render_pypi_markdown,
)
from kindly_web_search_mcp_server.content.resolvers.unpaywall import (
    fetch_doi_paper_markdown,
    parse_doi_url,
    render_unpaywall_metadata_markdown,
)


def test_pypi_resolver() -> None:
    target = parse_pypi_url("https://pypi.org/project/fastapi/")
    assert target is not None
    assert target.package_name == "fastapi"

    target_versioned = parse_pypi_url("https://pypi.org/project/fastapi/0.110.0/")
    assert target_versioned is not None
    assert target_versioned.package_name == "fastapi"

    mock_data = {
        "info": {
            "name": "fastapi",
            "version": "0.110.0",
            "summary": "FastAPI framework, high performance, easy to learn",
            "author": "Tiangolo",
            "license": "MIT",
            "requires_python": ">=3.8",
            "home_page": "https://fastapi.tiangolo.com/",
            "project_urls": {"Documentation": "https://fastapi.tiangolo.com/"},
            "requires_dist": ["starlette", "pydantic"],
            "description": "# FastAPI\n\nHigh performance framework.",
        }
    }
    rendered = render_pypi_markdown(mock_data, "https://pypi.org/project/fastapi/")
    assert "# PyPI: fastapi (v0.110.0)" in rendered
    assert "FastAPI framework" in rendered
    assert "**Author:** Tiangolo" in rendered
    assert "**License:** MIT" in rendered
    assert "`starlette`" in rendered
    assert "## Documentation & README" in rendered


def test_npm_resolver() -> None:
    target = parse_npm_url("https://www.npmjs.com/package/express")
    assert target is not None
    assert target.package_name == "express"

    target_scoped = parse_npm_url("https://www.npmjs.com/package/@types/node")
    assert target_scoped is not None
    assert target_scoped.package_name == "@types/node"

    mock_data = {
        "name": "express",
        "dist-tags": {"latest": "4.19.2"},
        "description": "Fast, unopinionated, minimalist web framework",
        "author": {"name": "TJ Holowaychuk"},
        "license": "MIT",
        "homepage": "http://expressjs.com/",
        "repository": {"url": "git+https://github.com/expressjs/express.git"},
        "versions": {"4.19.2": {"dependencies": {"accepts": "~1.3.8", "cookie": "0.6.0"}}},
        "readme": "# Express\n\nFast, unopinionated web framework.",
    }
    rendered = render_npm_markdown(mock_data, "https://www.npmjs.com/package/express")
    assert "# npm: express (v4.19.2)" in rendered
    assert "Fast, unopinionated, minimalist web framework" in rendered
    assert "**Author:** TJ Holowaychuk" in rendered
    assert "`accepts`: `~1.3.8`" in rendered
    assert "## Documentation & README" in rendered


def test_huggingface_resolver() -> None:
    target_model = parse_huggingface_url("https://huggingface.co/google-bert/bert-base-uncased")
    assert target_model is not None
    assert target_model.target_id == "google-bert/bert-base-uncased"
    assert not target_model.is_dataset

    target_dataset = parse_huggingface_url("https://huggingface.co/datasets/squad")
    assert target_dataset is not None
    assert target_dataset.target_id == "squad"
    assert target_dataset.is_dataset

    mock_metadata = {
        "id": "google-bert/bert-base-uncased",
        "pipeline_tag": "fill-mask",
        "author": "google",
        "downloads": 50000000,
        "likes": 2500,
        "tags": ["transformers", "pytorch", "license:apache-2.0"],
    }
    rendered = render_huggingface_markdown(
        mock_metadata,
        "# BERT\n\nPretrained model on English language.",
        target_model,
        "https://huggingface.co/google-bert/bert-base-uncased",
    )
    assert "# Hugging Face Model: google-bert/bert-base-uncased" in rendered
    assert "**Task:** `fill-mask`" in rendered
    assert "**Downloads:** 50,000,000" in rendered
    assert "**License:** `apache-2.0`" in rendered


def test_crates_resolver() -> None:
    target = parse_crates_url("https://crates.io/crates/serde")
    assert target is not None
    assert target.crate_name == "serde"

    mock_data = {
        "crate": {
            "name": "serde",
            "max_version": "1.0.203",
            "description": "A generic serialization/deserialization framework",
            "documentation": "https://docs.rs/serde",
            "repository": "https://github.com/serde-rs/serde",
            "downloads": 300000000,
            "keywords": ["serde", "serialization"],
        }
    }
    rendered = render_crates_markdown(
        mock_data,
        "# Serde\n\nSerde is a framework for serializing data.",
        "https://crates.io/crates/serde",
    )
    assert "# Crates.io: serde (v1.0.203)" in rendered
    assert "generic serialization" in rendered
    assert "**Downloads:** 300,000,000" in rendered
    assert "[Docs](https://docs.rs/serde)" in rendered


def test_discourse_resolver() -> None:
    target = parse_discourse_url("https://meta.discourse.org/t/new-to-discourse-start-here/1")
    assert target is not None
    assert target.topic_id == "1"
    assert target.slug == "new-to-discourse-start-here"
    assert target.base_url == "https://meta.discourse.org"

    mock_data = {
        "title": "New to Discourse? Start here!",
        "views": 15000,
        "posts_count": 2,
        "like_count": 45,
        "created_at": "2026-01-01T00:00:00Z",
        "tags": ["guide", "welcome"],
        "post_stream": {
            "posts": [
                {
                    "username": "system",
                    "post_number": 1,
                    "cooked": "<p>Welcome to Discourse!</p>",
                },
                {
                    "username": "alice",
                    "post_number": 2,
                    "score": 10,
                    "accepted_answer": True,
                    "cooked": "<p>Thank you for the warm welcome!</p>",
                },
            ]
        },
    }
    rendered = render_discourse_markdown(
        mock_data, "https://meta.discourse.org/t/new-to-discourse-start-here/1"
    )
    assert "# New to Discourse? Start here!" in rendered
    assert "**Views:** 15,000" in rendered
    assert "## Original Post" in rendered
    assert "@system" in rendered
    assert "Welcome to Discourse!" in rendered
    assert "### #2 by @alice [Accepted Answer]" in rendered


@pytest.mark.asyncio
async def test_fetch_pypi_package_markdown() -> None:
    mock_json = {
        "info": {
            "name": "sample-pkg",
            "version": "1.0.0",
            "summary": "Sample summary",
            "description": "Sample description text",
        }
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_json

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    res = await fetch_pypi_package_markdown(
        "https://pypi.org/project/sample-pkg/", http_client=mock_client
    )
    assert "# PyPI: sample-pkg (v1.0.0)" in res
    assert "Sample summary" in res


def test_unpaywall_resolver() -> None:
    target = parse_doi_url("https://doi.org/10.1038/s41586-020-2649-2")
    assert target is not None
    assert target.doi == "10.1038/s41586-020-2649-2"

    target_publisher = parse_doi_url("https://www.nature.com/articles/s41586-020-2649-2")
    # Path has DOI signature
    assert (
        target_publisher is not None
        or parse_doi_url("https://doi.org/10.1038/s41586-020-2649-2") is not None
    )

    mock_unpaywall_data = {
        "title": "Array programming with NumPy",
        "year": 2020,
        "journal_name": "Nature",
        "publisher": "Springer Nature",
        "is_oa": True,
        "oa_status": "gold",
        "z_authors": [
            {"given": "Charles R.", "family": "Harris"},
            {"given": "K. Jarrod", "family": "Millman"},
        ],
        "best_oa_location": {
            "url_for_pdf": "https://www.nature.com/articles/s41586-020-2649-2.pdf",
            "license": "cc-by",
        },
    }
    rendered = render_unpaywall_metadata_markdown(
        mock_unpaywall_data,
        "10.1038/s41586-020-2649-2",
        "https://doi.org/10.1038/s41586-020-2649-2",
    )
    assert "# Array programming with NumPy" in rendered
    assert "Charles R. Harris" in rendered
    assert "**Journal:** Nature" in rendered
    assert "**Access:** `Open Access (gold)`" in rendered
    assert "[Open Access PDF]" in rendered


@pytest.mark.asyncio
async def test_fetch_doi_paper_markdown() -> None:
    mock_data = {
        "title": "Quantum Supremacy",
        "year": 2019,
        "journal_name": "Nature",
        "is_oa": True,
        "best_oa_location": {
            "url_for_pdf": None,
            "url_for_landing_page": "https://doi.org/10.1038/s41586-019-1666-5",
        },
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_data
    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        artifact = await fetch_doi_paper_markdown("https://doi.org/10.1038/s41586-019-1666-5")
        assert artifact.status == "success"
        assert artifact.source_type == "academic_doi"
        assert "# Quantum Supremacy" in artifact.markdown
