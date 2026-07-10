from __future__ import annotations

import json

_INTENT_GUIDANCE = {
    "ai_coding_and_infrastructure": "Preserve package names, versions, APIs, error messages, repositories, and infrastructure terms; prefer documentation, issues, and release notes.",
    "comparison": "Preserve every compared item and the comparison dimension; make the contrast explicit without inventing an item.",
    "digital_humanities": "Preserve named works, people, archives, editions, corpora, dates, and scholarly terminology.",
    "social_media": "Preserve platform names, handles, trends, dates, and engagement terms.",
    "news": "Preserve the event, people, place, and date; prioritize recency and add a freshness hint when useful.",
    "general": "Create concise variants that improve retrieval without changing meaning or adding unsupported assumptions.",
}


def _example(keyword: str, neural: str, terms: list[str]) -> str:
    return json.dumps(
        {
            "variants": [
                {
                    "kind": "keyword_refined",
                    "target": "keyword",
                    "query": keyword,
                    "why": "Concise lexical formulation.",
                    "weight": 1.0,
                    "branch_type": "keyword_refined",
                    "must_keep_terms": terms,
                },
                {
                    "kind": "neural_refined",
                    "target": "neural",
                    "query": neural,
                    "why": "Natural semantic formulation.",
                    "weight": 1.0,
                    "branch_type": "neural_refined",
                    "must_keep_terms": terms,
                },
            ]
        }
    )


_INTENT_EXAMPLES = {
    "ai_coding_and_infrastructure": _example(
        "Python asyncio TaskGroup exception handling",
        "How does Python asyncio TaskGroup handle exceptions and cancellation?",
        ["asyncio", "TaskGroup"],
    ),
    "comparison": _example(
        "React 18 React 19 useEffect cleanup performance comparison",
        "Compare React 18 and React 19 useEffect cleanup behavior and performance.",
        ["React 18", "React 19", "useEffect"],
    ),
    "digital_humanities": _example(
        "digital humanities archive corpus metadata",
        "Find scholarly research about metadata practices for digital humanities archives and corpora.",
        [],
    ),
    "social_media": _example(
        "social media platform trend engagement metrics",
        "Find current social media trends and engagement metrics for the named platform.",
        [],
    ),
    "news": _example(
        "recent election results official reports",
        "Find recent reporting and official information about the election results.",
        [],
    ),
    "general": _example(
        "precise topic key entities",
        "Find reliable information about the user's stated topic and information need.",
        [],
    ),
}


def get_intent_instructions(intent: str) -> tuple[str, str]:
    """Return XML-tagged directives and a matching few-shot JSON example."""
    key = str(intent).lower()
    guidance = _INTENT_GUIDANCE.get(key, _INTENT_GUIDANCE["general"])
    example = _INTENT_EXAMPLES.get(key, _INTENT_EXAMPLES["general"])
    return f"<intent_rule>{guidance}</intent_rule>", f"<example>{example}</example>"
