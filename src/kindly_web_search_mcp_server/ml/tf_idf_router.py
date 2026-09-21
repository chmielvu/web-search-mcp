"""Offline quick-search mode router (TF-IDF + logistic regression).

Classifies a ``quick_web_search`` request into one of three execution
modes: ``web``, ``youtube``, or ``docs``. Inference runs fully offline with
scikit-learn; no embedding gateway or fastembed call is involved.

Routing combines three evidence sources:

1. **Field votes.** The ``quick_web_search`` contract maps request fields
   to modes (``query`` -> youtube, ``question``/``repo_url`` -> docs,
   ``search_queries``/``objective`` -> web). A filled field is strong
   structural evidence and votes for its mode.
2. **Weighted lexical rules.** Regex signals vote per individual field, so
   a co-occurrence inside one field ("api ... documentation") cannot be
   faked by words scattered across different fields. Weight-2 signals
   (search operators, explicit docs/video nouns, video URLs) decide on
   their own; weight-1 corroborators must combine with another vote.
3. **TF-IDF + LogisticRegression.** Seeds are curated from the real query
   corpus (``duckdb_data/training/query_understanding.jsonl``) for the web
   branch and from the tool-schema field shapes (video search terms,
   documentation questions) for the youtube and docs branches. Seeds are
   bundled in this module so the router needs no external artifacts.

A mode wins by votes when its total reaches ``RULE_WIN_WEIGHT``; otherwise
the model decides, abstaining to ``fallback`` when its best class
probability is below ``confidence_floor``. ``confidence`` on
:class:`ModeRoute` always reports the model's softmax score for the
executed label and is never inflated for rule-routed decisions.

Public surface:

- ``ModeRoute``: frozen result with label, scores, and provenance.
- ``route_quick_mode()``: one-shot sync classification of one request.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from sklearn.pipeline import Pipeline

QuickMode = Literal["web", "youtube", "docs"]

ROUTER_ID: Final = "ml.tf_idf_router:tfidf-logreg:v2"

# Minimum total vote weight for a mode to win without consulting the model.
RULE_WIN_WEIGHT: Final = 2

# Tie-break order for equal vote totals: docs > youtube > web. Docs and
# youtube evidence is mode-specific while web is the fallback surface, so
# web never wins a tie by priority.
_PRIORITY_ORDER: Final[tuple[QuickMode, ...]] = ("docs", "youtube", "web")

# GitHub repo URL (scheme optional: bare "github.com/owner/repo" text is
# common in real queries). Matches inline repo references; the dedicated
# ``repo_url`` argument remains the stronger structured docs signal.
_REPO_URL_RE: Final = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
    re.IGNORECASE,
)

# Weighted lexical signals evaluated per request field. Weight 3 marks
# decisive syntax (search operators are constructed for a web backend and
# outrank any docs-shaped wording around them); weight 2 decides alone;
# weight 1 corroborates another vote. Co-occurrence patterns are bounded to
# 80 characters so they cannot span unrelated topics inside one field.
_RULE_WEIGHTS: Final[dict[QuickMode, tuple[tuple[re.Pattern[str], int], ...]]] = {
    "web": (
        (
            re.compile(
                r"\b(?:site|inurl|related|cache|link|filetype|allintitle|allinurl):", re.IGNORECASE
            ),
            3,
        ),
    ),
    "docs": (
        (
            re.compile(
                r"\b(?:repo(?:sitory)?|package|library|module|api|sdk|cli)\b.{0,80}\b(?:docs?|documentation|usage|reference)\b",
                re.IGNORECASE,
            ),
            2,
        ),
        (
            re.compile(
                r"\b(?:docs?|documentation|api reference)\b.{0,80}\b(?:for|of|about)\b",
                re.IGNORECASE,
            ),
            2,
        ),
        (
            re.compile(
                r"\bhow (?:do|does|to|can)\b.{0,80}\b(?:repo|library|package|api|sdk|install)\b",
                re.IGNORECASE,
            ),
            2,
        ),
        (re.compile(r"\b(?:llms\.txt|api reference)\b", re.IGNORECASE), 2),
        (re.compile(r"\breadme\b", re.IGNORECASE), 1),
        (
            re.compile(
                r"\b(?:signature|parameters?|schema|type hints?|docstrings?)\b.{0,80}\b(?:of|for|in)\b",
                re.IGNORECASE,
            ),
            1,
        ),
        (_REPO_URL_RE, 1),
        (re.compile(r"\b(?:context7|deepwiki)\b", re.IGNORECASE), 1),
        (re.compile(r"\bchangelog\b", re.IGNORECASE), 1),
    ),
    "youtube": (
        (re.compile(r"\byoutube\.com/\b|\byoutu\.be\b", re.IGNORECASE), 2),
        (re.compile(r"\b(?:watch|view) (?:the )?video\b", re.IGNORECASE), 2),
        (re.compile(r"\b(?:tutorial|talk|demo|walkthrough|screencast)s?\b", re.IGNORECASE), 1),
        (re.compile(r"\b(?:channel|playlists?)\b", re.IGNORECASE), 1),
    ),
}

# Seeds for the statistical stage. Web seeds are verbatim queries from the
# real query corpus (keyword stacks, search operators, comparisons,
# objective-style imperatives). Youtube and docs seeds follow the field
# shapes the tool schema defines for those modes: bare video search terms
# and documentation questions/keyword docs lookups. Kept roughly balanced
# across the three classes so no class-weight compensation is needed.
_SEEDS: Final[dict[QuickMode, tuple[str, ...]]] = {
    "web": (
        "Streamlit DuckDB production dashboard best practices 2024",
        "Streamlit caching st.cache_data st.cache_resource best practices performance",
        "Tavily vs Exa vs Perplexity Sonar vs Linkup AI search API comparison benchmark",
        "site:github.com/brightdata/brightdata-mcp search_engine function schema query engine cursor",
        "site:exa.ai/docs/reference/verticals/code-for-coding-agents Exa code search type contents highlights includeDomains",
        "github.com pixeltable prompt engineering LLM studio source code",
        "RankGPT listwise reranker sliding window implementation github",
        "GPT Researcher multi-agent architecture planner executor publisher",
        "self-hosted web scraping application MCP backend AI agents Firecrawl Crawl4AI",
        "how developers manage large codebases when coding with AI context management",
        "AI coding assistant large codebase context window strategies tips",
        "LLM reranker vs cross-encoder RAG retrieval pipeline",
        "HyDE hypothetical document embeddings query rewriting cascade implementation github",
        "Gemini API release notes June 2026",
        "OpenTelemetry Python LoggingHandler OTLP logs Grafana Loki",
        "smolagents 30% fewer steps code agents benchmark",
        "GPT Researcher tree exploration depth breadth o3-mini cost 5 minutes",
        "current working methods to mint sessions on a VPS",
        "find workarounds for media download 403 bot-check so audio can be fetched",
        "compare YouTube transcript APIs pricing return type reliability 2026",
    ),
    "youtube": (
        "fastmcp tutorial",
        "agent design patterns video",
        "pydantic-ai walkthrough talk",
        "duckdb demo presentation",
        "python asyncio conference talk",
        "MCP server tutorial screencast",
        "rust tutorial video course",
        "system design interview walkthrough",
        "sqlite internals talk",
        "pytest fixtures demo",
        "kubernetes setup tutorial video",
        "networkx graph visualization demo",
        "fastapi authentication tutorial",
        "docker compose walkthrough",
        "watch the video about MCP security",
        "latest YouTube channel uploads about python packaging",
        "LangGraph course video playlist",
        "RAG pipelines explained video",
    ),
    "docs": (
        "How does DuckDB read_only mode interact with WAL replay",
        "What are the request and response contracts for the DeepWiki MCP ask_question tool",
        "What is the Pydantic v2 discriminated union API for Tag annotations",
        "How do you install the Playwright Python package and its browser binaries in an existing uv virtual environment on Windows",
        "How does fastmcp-tasks persist background tasks across restarts and what backends does it support",
        "What is the after_tool_execute hook signature on Hooks and how do Agent.instructions functions receive RunContext deps",
        "How do WebSearch capability tavily_search_tool and output_validator work for web search agents",
        "FastMCP middleware official documentation",
        "Tavily web search API docs",
        "Brave Search API documentation AI web search provider",
        "Cerebras API documentation chat completions parameters",
        "Firecrawl search endpoint extract endpoint API documentation",
        "yt-transcript-pro python library YouTube transcript extraction API usage",
        '"llama-index-vector-stores-duckdb" usage example',
        "DuckDB process_logs schema",
        "typer Annotated option help and multi-value option docs",
        "Exact CLI invocation to validate untrusted Markdown via stdin with JSON output",
        "Context7 official MCP server resolve-library-id query-docs schema",
    ),
}


@dataclass(frozen=True)
class ModeRoute:
    """One routing decision with per-mode scores and provenance.

    ``requested_mode`` records what the caller asked for; the executed mode
    is always :attr:`label`. It is informational only (analytics and
    provenance) and never changes routing behavior. ``confidence`` is the
    model's softmax score for the executed label, including rule-routed
    decisions where it records agreement rather than the routing basis.
    """

    label: QuickMode
    scores: dict[str, float]
    source: str
    confidence: float
    matched_rules: tuple[str, ...] = field(default_factory=tuple)
    requested_mode: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable provenance payload."""
        return {
            "label": self.label,
            "scores": dict(self.scores),
            "source": self.source,
            "confidence": round(self.confidence, 4),
            "matched_rules": list(self.matched_rules),
            "requested_mode": self.requested_mode,
            "router_id": ROUTER_ID,
        }


def _normalise(text: str) -> str:
    """Lowercase and collapse whitespace; the view is versioned by ROUTER_ID."""
    return re.sub(r"\s+", " ", text.casefold()).strip()


@functools.lru_cache(maxsize=1)
def _fit_model() -> Pipeline:
    """Fit TF-IDF + LogisticRegression once; sklearn is required, not optional."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    texts: list[str] = []
    labels: list[str] = []
    for label, samples in _SEEDS.items():
        texts.extend(_normalise(sample) for sample in samples)
        labels.extend([label] * len(samples))

    model = Pipeline(
        [
            (
                "tfidf",
                # No stop-word list: question markers ("how", "what", "does")
                # are the structural signal that separates docs-mode questions
                # from keyword queries, and IDF already downweights ubiquitous
                # terms in a corpus this small.
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    sublinear_tf=True,
                    min_df=1,
                ),
            ),
            (
                "clf",
                LogisticRegression(C=1.0, max_iter=2000, random_state=0),
            ),
        ]
    )
    model.fit(texts, labels)
    return model


def _field_votes(
    fields: dict[str, str],
    repo_url: str | None,
) -> tuple[dict[QuickMode, int], list[str]]:
    """Vote per mode from the tool contract's field-to-mode mapping."""
    votes: dict[QuickMode, int] = {"docs": 0, "youtube": 0, "web": 0}
    matched: list[str] = []
    if repo_url and repo_url.strip():
        votes["docs"] += 3
        matched.append("field:docs:repo_url")
    if fields["question"]:
        votes["docs"] += 1
        matched.append("field:docs:question")
    if fields["query"]:
        votes["youtube"] += 1
        matched.append("field:youtube:query")
    if fields["search_queries"]:
        votes["web"] += 1
        matched.append("field:web:search_queries")
    elif fields["objective"]:
        votes["web"] += 1
        matched.append("field:web:objective")
    return votes, matched


def _rule_votes(
    fields: dict[str, str],
) -> tuple[dict[QuickMode, int], list[str]]:
    """Apply weighted lexical rules to each field independently."""
    votes: dict[QuickMode, int] = {"docs": 0, "youtube": 0, "web": 0}
    matched: list[str] = []
    for field_text in fields.values():
        if not field_text:
            continue
        for mode, patterns in _RULE_WEIGHTS.items():
            for pattern, weight in patterns:
                if pattern.search(field_text):
                    votes[mode] += weight
                    matched.append(f"rule:{mode}:w{weight}:{pattern.pattern[:40]}")
    return votes, matched


def route_quick_mode(
    *,
    search_queries: Sequence[str] | None = None,
    objective: str | None = None,
    query: str | None = None,
    question: str | None = None,
    repo_url: str | None = None,
    fallback: QuickMode = "web",
    confidence_floor: float = 0.45,
    requested_mode: str | None = None,
) -> ModeRoute:
    """Route one ``quick_web_search`` request to ``web``/``youtube``/``docs``.

    Args:
        search_queries: Keyword queries supplied for web-mode lookups.
        objective: Natural-language goal supplied for web-mode lookups.
        query: Video search term supplied for youtube-mode lookups.
        question: Documentation question supplied for docs-mode lookups.
        repo_url: Caller-supplied repository URL; decisive docs signal.
        fallback: Mode returned when the model abstains.
        confidence_floor: Minimum best-class probability before abstaining.
        requested_mode: Caller's informational mode hint, recorded in
            provenance only.

    Returns:
        ``ModeRoute`` with label, per-mode scores, and provenance. Scores
        are softmax outputs of this router version, not calibrated
        probabilities across engines. An all-blank request routes to
        ``fallback`` instead of raising.

    Raises:
        ValueError: If ``confidence_floor`` is not in ``[0, 1]``.
    """
    if not 0.0 <= confidence_floor <= 1.0:
        raise ValueError("confidence_floor must be within [0, 1].")
    fields: dict[str, str] = {
        "search_queries": _normalise(" ".join(search_queries or ())),
        "objective": _normalise(objective or ""),
        "query": _normalise(query or ""),
        "question": _normalise(question or ""),
    }
    combined = " ".join(text for text in fields.values() if text)
    model = _fit_model()
    if combined:
        proba = model.predict_proba([combined])[0]
        classes = list(model.named_steps["clf"].classes_)
        all_scores: dict[QuickMode, float] = {
            cls: float(p) for cls, p in zip(classes, proba, strict=True)
        }
    else:
        all_scores = dict.fromkeys(_PRIORITY_ORDER, 0.0)
    best: QuickMode = max(all_scores, key=all_scores.__getitem__)
    scores: dict[str, float] = {str(mode): p for mode, p in all_scores.items()}

    field_v, field_m = _field_votes(fields, repo_url)
    rule_v, rule_m = _rule_votes(fields)
    votes: dict[QuickMode, int] = {mode: field_v[mode] + rule_v[mode] for mode in _PRIORITY_ORDER}
    matched = field_m + rule_m

    # max() keeps the first maximal element, so _PRIORITY_ORDER defines the
    # documented tie-break: docs > youtube > web.
    vote_best: QuickMode = max(_PRIORITY_ORDER, key=votes.__getitem__)
    if votes[vote_best] >= RULE_WIN_WEIGHT:
        return ModeRoute(
            label=vote_best,
            scores=scores,
            source="rules",
            confidence=all_scores[vote_best],
            matched_rules=tuple(matched),
            requested_mode=requested_mode,
        )
    # Blank request text with no vote evidence is a pure abstain; with votes
    # (e.g. only ``repo_url`` set) the vote path still decides.
    if not combined or all_scores[best] < confidence_floor:
        return ModeRoute(
            label=fallback,
            scores=scores,
            source="model:abstained",
            confidence=all_scores[best],
            matched_rules=tuple(matched),
            requested_mode=requested_mode,
        )
    return ModeRoute(
        label=best,
        scores=scores,
        source="model",
        confidence=all_scores[best],
        matched_rules=tuple(matched),
        requested_mode=requested_mode,
    )


__all__ = ["ROUTER_ID", "ModeRoute", "QuickMode", "route_quick_mode"]
