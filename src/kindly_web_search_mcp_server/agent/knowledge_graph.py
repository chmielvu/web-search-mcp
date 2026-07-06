from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlparse

import networkx as nx
from langchain_core.messages import AIMessage, ToolMessage

from .models import ResearchGraphSummary, ResearchSource

_URL_KEYS = {"url", "link", "fetched_url", "input_url", "page_link", "original_url"}


def _coerce_payload(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


def _domain(url: str) -> str | None:
    parsed = urlparse(url)
    return parsed.netloc or None


def _iter_urls(payload: Any) -> Iterable[str]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in _URL_KEYS and isinstance(value, str) and value.strip():
                yield value.strip()
            else:
                yield from _iter_urls(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_urls(item)


@dataclass
class ResearchKnowledgeGraph:
    query: str
    graph: nx.MultiDiGraph = field(default_factory=nx.MultiDiGraph)
    sources: dict[str, ResearchSource] = field(default_factory=dict)
    tool_calls: Counter[str] = field(default_factory=Counter)
    title_variants: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    fetched_urls: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.graph.add_node("query:root", kind="query", text=self.query)

    def record_tool_call(self, tool_name: str) -> None:
        self.tool_calls[tool_name] += 1
        self.graph.add_node(f"tool:{tool_name}", kind="tool", name=tool_name)
        self.graph.add_edge("query:root", f"tool:{tool_name}", relation="used")

    def record_tool_plan(self, tool_name: str) -> None:
        self.graph.add_node(f"tool:{tool_name}", kind="tool", name=tool_name)
        self.graph.add_edge("query:root", f"tool:{tool_name}", relation="planned")

    def record_source(
        self,
        *,
        tool: str,
        url: str,
        title: str | None = None,
        snippet: str | None = None,
        score: float | None = None,
        kind: str = "search",
    ) -> None:
        normalized_url = url.strip()
        if not normalized_url:
            return
        domain = _domain(normalized_url)
        source = self.sources.get(normalized_url)
        if source is None:
            self.sources[normalized_url] = ResearchSource(
                title=title,
                url=normalized_url,
                snippet=snippet,
                tool=tool,
                domain=domain,
                score=score,
                kind=kind,
            )
        elif title and not source.title:
            source.title = title
        elif snippet and not source.snippet:
            source.snippet = snippet
        elif score is not None and source.score is None:
            source.score = score

        if title:
            self.title_variants[normalized_url].add(title)
        if kind == "fetch":
            self.fetched_urls.add(normalized_url)

        self.graph.add_node(
            normalized_url,
            kind=kind,
            title=title,
            snippet=snippet,
            domain=domain,
            tool=tool,
            score=score,
        )
        self.graph.add_edge(f"tool:{tool}", normalized_url, relation="returned")
        if domain:
            self.graph.add_node(f"domain:{domain}", kind="domain", domain=domain)
            self.graph.add_edge(normalized_url, f"domain:{domain}", relation="on_domain")

    def ingest_payload(self, tool_name: str, payload: Any) -> None:
        payload = _coerce_payload(payload)
        if isinstance(payload, dict):
            urls = list(_iter_urls(payload))
            title = payload.get("title") if isinstance(payload.get("title"), str) else None
            snippet = None
            if isinstance(payload.get("snippet"), str):
                snippet = payload["snippet"]
            elif isinstance(payload.get("content"), str):
                snippet = payload["content"][:300]
            elif isinstance(payload.get("page_content"), str):
                snippet = payload["page_content"][:300]
            score = None
            for key in ("score", "raw_score", "provider_score"):
                if isinstance(payload.get(key), (int, float)):
                    score = float(payload[key])
                    break
            if urls:
                kind = "fetch" if "page_content" in payload or "window" in payload else "search"
                for url in urls:
                    self.record_source(
                        tool=tool_name,
                        url=url,
                        title=title,
                        snippet=snippet,
                        score=score,
                        kind=kind,
                    )
            for value in payload.values():
                if isinstance(value, (dict, list)):
                    self.ingest_payload(tool_name, value)
        elif isinstance(payload, list):
            for item in payload:
                self.ingest_payload(tool_name, item)

    def ingest_messages(self, messages: list[Any]) -> None:
        for message in messages:
            if isinstance(message, AIMessage):
                for tool_call in getattr(message, "tool_calls", []) or []:
                    name = tool_call.get("name")
                    if isinstance(name, str) and name.strip():
                        self.record_tool_plan(name.strip())
            elif isinstance(message, ToolMessage):
                tool_name = getattr(message, "name", None)
                if isinstance(tool_name, str) and tool_name.strip():
                    self.record_tool_call(tool_name.strip())
                    self.ingest_payload(tool_name.strip(), message.content)

    def source_records(self) -> list[ResearchSource]:
        return sorted(
            self.sources.values(),
            key=lambda item: (
                -(item.score or 0.0),
                item.title or "",
                item.url,
            ),
        )

    def potential_conflicts(self) -> list[str]:
        conflicts: list[str] = []
        for url, titles in self.title_variants.items():
            if len(titles) > 1:
                conflicts.append(f"{url}: {', '.join(sorted(titles))}")
        return conflicts

    def summary(self) -> ResearchGraphSummary:
        domains = {source.domain for source in self.sources.values() if source.domain}
        return ResearchGraphSummary(
            node_count=self.graph.number_of_nodes(),
            edge_count=self.graph.number_of_edges(),
            tool_count=len(self.tool_calls),
            url_count=len(self.sources),
            domain_count=len(domains),
            source_urls=sorted(self.sources),
            fetched_urls=sorted(self.fetched_urls),
            tool_calls=dict(self.tool_calls),
            potential_conflicts=self.potential_conflicts(),
        )
