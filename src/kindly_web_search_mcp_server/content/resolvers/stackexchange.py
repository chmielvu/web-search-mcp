from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ..documents import build_thread_document
from ..http_utils import raise_for_status, request_with_redirect_validation
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

STACKEXCHANGE_API_BASE_URL = "https://api.stackexchange.com/2.3"

# Default to the official named filter that includes `body` (HTML) for both questions and answers.
# If `body_markdown` is desired, provide a custom filter via `STACKEXCHANGE_FILTER`.
DEFAULT_STACKEXCHANGE_FILTER = "withbody"


@dataclass(frozen=True)
class StackExchangeTarget:
    site: str
    question_id: int | None
    answer_id: int | None


class StackExchangeError(RuntimeError):
    pass


def _derive_site_parameter(host: str) -> str | None:
    host = host.lower()

    # Meta exception: meta.stackexchange.com -> site=meta (per provided docs)
    if host == "meta.stackexchange.com":
        return "meta"

    # Meta communities: meta.<community>.com -> meta.<community>
    if host.startswith("meta.") and host.endswith(".com"):
        return host.removesuffix(".com")

    # stackexchange subdomains: <community>.stackexchange.com -> <community>
    if host.endswith(".stackexchange.com"):
        return host[: -len(".stackexchange.com")].split(".")[0]

    # Common communities: stackoverflow.com, superuser.com, serverfault.com, askubuntu.com, etc.
    if host.endswith(".com"):
        return host.removesuffix(".com")

    return None


_QUESTION_RE = re.compile(r"/(?:questions|q)/(\d+)(?:/|$)")
_ANSWER_RE = re.compile(r"/a/(\d+)(?:/|$)")


def parse_stackexchange_url(url: str) -> StackExchangeTarget:
    """Parse a StackExchange network URL to determine site and question/answer id."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise StackExchangeError("URL has no hostname.")

    site = _derive_site_parameter(host)
    if not site:
        raise StackExchangeError(f"Unsupported StackExchange host: {host!r}")

    path = parsed.path or ""

    m_q = _QUESTION_RE.search(path)
    if m_q:
        return StackExchangeTarget(site=site, question_id=int(m_q.group(1)), answer_id=None)

    m_a = _ANSWER_RE.search(path)
    if m_a:
        return StackExchangeTarget(site=site, question_id=None, answer_id=int(m_a.group(1)))

    raise StackExchangeError("URL is not a recognized StackExchange question/answer URL.")


# ---------------------------------------------------------------------------
# Raw producer entry points (registry-facing).
# ---------------------------------------------------------------------------


def match_stackexchange(parsed: ParsedURL) -> ResolverTarget | None:
    try:
        target = parse_stackexchange_url(parsed.url)
    except StackExchangeError:
        return None
    values: dict[str, str] = {"site": target.site}
    if target.question_id is not None:
        values["question_id"] = str(target.question_id)
    if target.answer_id is not None:
        values["answer_id"] = str(target.answer_id)
    return ResolverTarget(url=parsed.url, kind="stackexchange", values=values)


async def fetch_stackexchange_raw(target: ResolverTarget, ctx: FetchContext) -> RawDocument:
    """Fetch a StackExchange thread and return a :class:`RawDocument` with a thread body."""

    site = target.values.get("site")
    if not site:
        raise AcquisitionError(code="bad_target", message="StackExchange target missing site")
    question_id = target.values.get("question_id")
    answer_id = target.values.get("answer_id")
    if not question_id and not answer_id:
        raise AcquisitionError(
            code="bad_target", message="StackExchange target missing question/answer id"
        )

    params: dict[str, Any] = {"site": site, "filter": DEFAULT_STACKEXCHANGE_FILTER}
    if question_id:
        questions_url = f"{STACKEXCHANGE_API_BASE_URL}/questions/{question_id}"
    else:
        # For answer-only URLs we look up the parent question id first.
        questions_url = (
            f"{STACKEXCHANGE_API_BASE_URL}/answers/{answer_id}"
            if answer_id
            else f"{STACKEXCHANGE_API_BASE_URL}/questions/{question_id}"
        )
    if answer_id and not question_id:
        params["filter"] += ";answerId"

    response = await request_with_redirect_validation(
        ctx,
        questions_url,
        params=params,
        follow_redirects=True,
    )
    raise_for_status(response, what="StackExchange API")
    payload = response.json()
    if not isinstance(payload, dict):
        raise AcquisitionError(
            code="json_parse_error", message="StackExchange payload not an object"
        )
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        raise AcquisitionError(code="empty_response", message="StackExchange returned no items")
    question_payload = items[0]
    if not isinstance(question_payload, dict):
        raise AcquisitionError(
            code="bad_question", message="StackExchange question payload missing"
        )

    messages: list[ThreadMessage] = []
    root_id = f"q_{question_payload.get('question_id') or question_id}"
    title = str(question_payload.get("title") or f"StackExchange {question_id or answer_id}")
    messages.append(
        ThreadMessage(
            id=root_id,
            role="question",
            body=str(question_payload.get("body") or ""),
            body_format="html",
            author=str(question_payload.get("owner", {}).get("display_name"))
            if isinstance(question_payload.get("owner"), dict)
            else None,
            created_at=str(question_payload.get("creation_date") or "") or None,
            score=question_payload.get("score")
            if isinstance(question_payload.get("score"), int)
            else None,
            accepted=bool(question_payload.get("accepted_answer_id")),
            permalink=str(question_payload.get("link") or "") or None,
            parent_id=None,
        )
    )
    answers = question_payload.get("answers") or []
    if isinstance(answers, list):
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            aid = f"a_{answer.get('answer_id')}"
            body_text = str(answer.get("body") or "")
            if not body_text:
                continue
            messages.append(
                ThreadMessage(
                    id=aid,
                    role="answer" if answer.get("is_accepted") else "comment",
                    body=body_text,
                    body_format="html",
                    author=str(answer.get("owner", {}).get("display_name"))
                    if isinstance(answer.get("owner"), dict)
                    else None,
                    created_at=str(answer.get("creation_date") or "") or None,
                    score=answer.get("score") if isinstance(answer.get("score"), int) else None,
                    accepted=bool(answer.get("is_accepted")),
                    permalink=str(answer.get("share_link") or "") or None,
                    parent_id=root_id,
                )
            )

    thread: ThreadDocument = build_thread_document(
        title=title,
        url=target.url,
        messages=tuple(messages),
        metadata={
            "site": site,
            "question_id": question_id,
            "answer_id": answer_id,
            "answer_count": len(
                [m for m in messages if m.role in {"answer", "comment"} and m.id.startswith("a_")]
            ),
        },
    )
    diagnostics = (
        Diagnostic(
            phase="acquisition",
            code="stackexchange_fetched",
            message=f"StackExchange {site} q={question_id or '-'} a={answer_id or '-'} ({len(messages)} messages)",
        ),
    )
    return RawDocument(
        input_url=target.url,
        fetched_url=target.url,
        source_type="stackexchange",
        fetch_backend="stackexchange_api",
        body=thread,
        title=title,
        metadata={
            "site": site,
            "question_id": question_id,
            "answer_id": answer_id,
            "answer_count": thread.metadata.get("answer_count"),
        },
        diagnostics=diagnostics,
        complete=bool(messages),
        scope="full",
    )
