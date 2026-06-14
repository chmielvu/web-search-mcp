from __future__ import annotations

import json
import re
from urllib.parse import urlparse
from typing import Any

from ..models import WebSearchResult

_FIELD_PATTERN = re.compile(r"^(Title|URL|Description|Snippet)\s*:\s*(.+)$", re.I)
_MARKDOWN_LINK_PATTERN = re.compile(
    r"^\s*(?:[-*+]\s*)?(?P<title>.+?)\s*\((?P<link>https?://[^)\s]+)\)"
    r"(?:\s*[-:–—]\s*(?P<snippet>.+))?$"
)
_YANDEX_RESULT_BULLET_PATTERN = re.compile(
    r"^\s*\*\s*\[(?P<label>[^\]]*)\]\((?P<link>https?://[^)\s]+)\)\s*$"
)


def _strip_inline_markup(text: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[(.*?)\]\((https?://[^)\s]+)\)", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_item(item: dict[str, Any]) -> WebSearchResult | None:
    title = item.get("title") or item.get("name")
    link = item.get("link") or item.get("url")
    snippet = item.get("snippet") or item.get("description") or item.get("summary")
    if not isinstance(title, str) or not title.strip() or not isinstance(link, str) or not link.strip():
        return None
    if not isinstance(snippet, str):
        snippet = ""
    return WebSearchResult(title=title.strip(), link=link.strip(), snippet=snippet)


def _parse_markdown_results(text: str) -> list[WebSearchResult]:
    results: list[WebSearchResult] = []
    current: dict[str, Any] = {}

    def flush() -> None:
        title = current.get("title")
        link = current.get("link")
        if isinstance(title, str) and isinstance(link, str) and title.strip() and link.strip():
            snippet_parts = current.get("snippet_parts", [])
            snippet = " ".join(part.strip() for part in snippet_parts if part.strip())
            results.append(WebSearchResult(title=title.strip(), link=link.strip(), snippet=snippet))
        current.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if line.startswith("```"):
            continue

        field_match = _FIELD_PATTERN.match(line)
        if field_match:
            field = field_match.group(1).casefold()
            value = field_match.group(2).strip()
            if field == "title":
                current["title"] = value
            elif field == "url":
                current["link"] = value
            else:
                current.setdefault("snippet_parts", []).append(value)
            continue

        link_match = _MARKDOWN_LINK_PATTERN.match(line)
        if link_match:
            flush()
            results.append(
                WebSearchResult(
                    title=link_match.group("title").strip(),
                    link=link_match.group("link").strip(),
                    snippet=(link_match.group("snippet") or "").strip(),
                )
            )
            continue

        if line.startswith("http://") or line.startswith("https://"):
            current["link"] = line
            continue

        if "title" not in current:
            current["title"] = line
        else:
            current.setdefault("snippet_parts", []).append(line)

    flush()
    return results


def _parse_yandex_markdown_results(text: str) -> list[WebSearchResult]:
    results: list[WebSearchResult] = []
    current: dict[str, Any] = {}
    in_result = False

    def flush() -> None:
        title = current.get("title")
        link = current.get("link")
        if isinstance(title, str) and isinstance(link, str) and title.strip() and link.strip():
            netloc = urlparse(link).netloc.casefold()
            if "yabs.yandex" in netloc or "passport.yandex" in netloc:
                current.clear()
                return
            snippet_parts = current.get("snippet_parts", [])
            snippet = " ".join(part.strip() for part in snippet_parts if part.strip())
            results.append(WebSearchResult(title=title.strip(), link=link.strip(), snippet=snippet))
        current.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if in_result:
                current.setdefault("snippet_parts", []).append("")
            continue
        if line.startswith("```"):
            continue

        bullet_match = _YANDEX_RESULT_BULLET_PATTERN.match(line)
        if bullet_match:
            flush()
            in_result = True
            current["link"] = bullet_match.group("link").strip()
            continue

        if not in_result:
            continue

        if line.startswith("## "):
            title = _strip_inline_markup(line[3:])
            if title:
                current["title"] = title
            continue

        if line.startswith("# "):
            title = _strip_inline_markup(line[2:])
            if title:
                current["title"] = title
            continue

        if line.startswith("http://") or line.startswith("https://"):
            current["link"] = line
            continue

        if line.startswith("[") and line.endswith("]"):
            continue
        if line.startswith("![]("):
            continue
        if line in {"Найти", "Все", "Картинки", "Видео", "Карты", "Переводчик"}:
            continue

        text_line = _strip_inline_markup(line)
        if text_line:
            if "title" not in current and (
                line.startswith("## ")
                or " " in text_line
                or "**" in raw_line
                or "[" in raw_line
            ):
                current["title"] = text_line
            else:
                current.setdefault("snippet_parts", []).append(text_line)

    flush()
    return results


def parse_result_payload(data: Any) -> list[WebSearchResult]:
    if isinstance(data, dict):
        organic = data.get("organic") or data.get("results") or data.get("items") or []
    elif isinstance(data, list):
        organic = data
    else:
        return []

    if not isinstance(organic, list):
        return []

    results: list[WebSearchResult] = []
    for item in organic:
        if not isinstance(item, dict):
            continue
        parsed = _parse_item(item)
        if parsed is not None:
            results.append(parsed)
    return results


def parse_result_text(text: str) -> list[WebSearchResult]:
    try:
        data = json.loads(text)
    except (ValueError, TypeError, json.JSONDecodeError):
        if "Яндекс:" in text or "yandex.kz" in text or "yandex.ru" in text:
            yandex_results = _parse_yandex_markdown_results(text)
            if yandex_results:
                return yandex_results
        return _parse_markdown_results(text)
    return parse_result_payload(data)


def describe_upstream_error(text: str) -> str | None:
    try:
        payload = json.loads(text)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    status_code = payload.get("status_code")
    if not isinstance(status_code, int) or status_code < 400:
        return None

    headers = payload.get("headers")
    header_message = ""
    if isinstance(headers, dict):
        header_message = (
            headers.get("x-brd-err-msg")
            or headers.get("proxy-status")
            or headers.get("content-type")
            or ""
        )

    body = payload.get("body")
    if isinstance(body, str) and body.strip():
        body = body.strip()
        return f"BrightData upstream {status_code}: {header_message or body[:240]}"

    return f"BrightData upstream {status_code}: {header_message or 'unknown error'}"
