from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
import pytest

from kindly_web_search_mcp_server.content.resolvers.document import (
    DocumentConversionError,
    _convert_csv_to_markdown,
    _convert_ipynb_to_markdown,
    _convert_pdf_to_markdown,
    fetch_document_markdown,
    get_doc_source_type,
    is_document_url,
    rewrite_document_url,
)
from kindly_web_search_mcp_server.content.safe_fetch import SafeFetchResult


def test_is_document_url() -> None:
    assert is_document_url("https://example.com/file.pdf")
    assert is_document_url("https://example.com/path/doc.docx")
    assert is_document_url("https://example.com/slides.pptx")
    assert is_document_url("https://example.com/data.xlsx")
    assert is_document_url("https://example.com/book.epub")
    assert is_document_url("https://example.com/notebook.ipynb")
    assert is_document_url("https://example.com/table.csv")
    assert is_document_url("https://example.com/table.tsv")
    assert is_document_url("https://docs.google.com/document/d/1abc123-xyz/edit")
    assert is_document_url("https://docs.google.com/spreadsheets/d/1abc123-xyz/edit")

    # Non-document URLs
    assert not is_document_url("https://example.com/page.html")
    assert not is_document_url("https://example.com/script.py")
    assert not is_document_url("https://example.com/readme.md")


def test_convert_pdf_to_markdown() -> None:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), "Hello PyMuPDF Document Extraction")
    pdf_bytes = doc.write()
    doc.close()

    rendered = _convert_pdf_to_markdown(pdf_bytes, "https://example.com/doc.pdf")
    assert "# PDF Document" in rendered
    assert "Hello PyMuPDF Document Extraction" in rendered


def test_rewrite_document_url() -> None:
    doc_url = "https://docs.google.com/document/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit"
    rewritten_doc = rewrite_document_url(doc_url)
    assert (
        rewritten_doc
        == "https://docs.google.com/document/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/export?format=txt"
    )

    sheet_url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit#gid=0"
    rewritten_sheet = rewrite_document_url(sheet_url)
    assert (
        rewritten_sheet
        == "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/export?format=csv"
    )

    normal_url = "https://example.com/doc.pdf"
    assert rewrite_document_url(normal_url) == normal_url


def test_convert_ipynb_to_markdown() -> None:
    sample_nb = {
        "metadata": {
            "title": "Sample Analysis Notebook",
            "language_info": {"name": "python"},
        },
        "cells": [
            {
                "cell_type": "markdown",
                "source": ["# Title\n", "This is an exploratory analysis."],
            },
            {
                "cell_type": "code",
                "execution_count": 1,
                "source": ["import numpy as np\n", "x = np.array([1, 2, 3])\n", "print(x)"],
                "outputs": [
                    {
                        "output_type": "stream",
                        "text": ["[1 2 3]\n"],
                    }
                ],
            },
            {
                "cell_type": "code",
                "execution_count": 2,
                "source": ["x.sum()"],
                "outputs": [
                    {
                        "output_type": "execute_result",
                        "data": {"text/plain": ["6"]},
                    }
                ],
            },
        ],
    }

    nb_json = json.dumps(sample_nb)
    rendered = _convert_ipynb_to_markdown(nb_json, "https://example.com/test.ipynb")

    assert "# Sample Analysis Notebook" in rendered
    assert "This is an exploratory analysis." in rendered
    assert "```python\nimport numpy as np" in rendered
    assert "[1]:" in rendered
    assert "[1 2 3]" in rendered
    assert "**Output:**" in rendered
    assert "6" in rendered


def test_convert_csv_to_markdown() -> None:
    csv_text = "Name,Age,Role\nAlice,30,Engineer\nBob,25,Designer\nCharlie,35,Manager"
    rendered = _convert_csv_to_markdown(csv_text, "https://example.com/team.csv")

    assert "# Data Table" in rendered
    assert "| Name | Age | Role |" in rendered
    assert "| --- | --- | --- |" in rendered
    assert "| Alice | 30 | Engineer |" in rendered
    assert "| Bob | 25 | Designer |" in rendered
    assert "| Charlie | 35 | Manager |" in rendered


def test_convert_csv_to_markdown_accepts_crlf() -> None:
    csv_text = "Name,Age\rAlice,30\rBob,25\r"
    rendered = _convert_csv_to_markdown(csv_text, "https://example.com/team.csv")

    assert "| Name | Age |" in rendered
    assert "| Alice | 30 |" in rendered
    assert "| Bob | 25 |" in rendered


def test_get_doc_source_type() -> None:
    assert get_doc_source_type("https://example.com/file.pdf") == "pdf"
    assert get_doc_source_type("https://example.com/file.docx") == "docx"
    assert get_doc_source_type("https://example.com/file.ipynb") == "ipynb"
    assert get_doc_source_type("https://example.com/file.csv") == "csv"
    assert get_doc_source_type("https://docs.google.com/document/d/123/edit") == "docx"
    assert get_doc_source_type("https://docs.google.com/spreadsheets/d/123/edit") == "csv"


@pytest.mark.asyncio
async def test_fetch_document_markdown_csv() -> None:
    mock_csv_body = b"Language,Paradigms,Creator\nPython,Multi-paradigm,Guido van Rossum\nRust,Multi-paradigm,Graydon Hoare"
    mock_result = SafeFetchResult(
        input_url="https://example.com/languages.csv",
        fetched_url="https://example.com/languages.csv",
        content_type="text/csv",
        body=mock_csv_body,
        text=mock_csv_body.decode("utf-8"),
        is_pdf=False,
        doc_type="csv",
    )

    with patch(
        "kindly_web_search_mcp_server.content.resolvers.document.safe_fetch_url",
        new_callable=AsyncMock,
    ) as mock_fetch:
        mock_fetch.return_value = mock_result
        artifact = await fetch_document_markdown("https://example.com/languages.csv")

        assert artifact.status == "success"
        assert artifact.source_type == "csv"
        assert artifact.fetch_backend == "doc_converter_csv"
        assert "| Language | Paradigms | Creator |" in artifact.markdown
        assert "| Python | Multi-paradigm | Guido van Rossum |" in artifact.markdown


@pytest.mark.asyncio
async def test_office_conversion_success_is_not_placeholder() -> None:
    mock_result = SafeFetchResult(
        input_url="https://example.com/report.docx",
        fetched_url="https://example.com/report.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        body=b"office-bytes",
        text="",
        is_pdf=False,
        doc_type="docx",
    )
    with (
        patch(
            "kindly_web_search_mcp_server.content.resolvers.document.safe_fetch_url",
            new_callable=AsyncMock,
            return_value=mock_result,
        ),
        patch(
            "kindly_web_search_mcp_server.content.resolvers.document._convert_office_with_markitdown",
            return_value="Converted Office text",
        ),
    ):
        artifact = await fetch_document_markdown("https://example.com/report.docx")

    assert artifact.status == "success"
    assert artifact.source_type == "docx"
    assert "Converted Office text" in artifact.markdown
    assert "Unable to extract" not in artifact.markdown


@pytest.mark.asyncio
async def test_office_conversion_failure_is_explicit() -> None:
    mock_result = SafeFetchResult(
        input_url="https://example.com/report.docx",
        fetched_url="https://example.com/report.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        body=b"office-bytes",
        text="",
        is_pdf=False,
        doc_type="docx",
    )
    with (
        patch(
            "kindly_web_search_mcp_server.content.resolvers.document.safe_fetch_url",
            new_callable=AsyncMock,
            return_value=mock_result,
        ),
        patch(
            "kindly_web_search_mcp_server.content.resolvers.document._convert_office_with_markitdown",
            side_effect=DocumentConversionError(
                "office_dependency_missing",
                "markitdown is unavailable",
            ),
        ),
    ):
        artifact = await fetch_document_markdown("https://example.com/report.docx")

    assert artifact.status == "error"
    assert artifact.source_type == "docx"
    assert artifact.error is not None
    assert artifact.error.code == "office_dependency_missing"


def test_office_converter_rejects_html_disguised_as_xlsx() -> None:
    from kindly_web_search_mcp_server.content.resolvers.document import (
        _convert_office_with_markitdown,
    )

    with pytest.raises(DocumentConversionError, match="valid ZIP-based"):
        _convert_office_with_markitdown(b"<html>not a workbook</html>", "report.xlsx")
