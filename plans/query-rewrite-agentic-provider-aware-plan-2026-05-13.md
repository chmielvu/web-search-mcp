# Query Rewrite Prompt Implementation Spec

**Scope**: Replace the current broken query rewrite prompt stack for `web_search`.

**Main fix**: produce real prompt builders and validators for AI-agent bag-of-words web search queries. Keyword engines and neural/grounded engines get different query text.

## Immediate Problem

The current prompt is broken because it teaches invalid enum values:

```text
current prompt: docs, community, keywords
current model: official_docs, community_issues, expanded, focused, entity_a, entity_b
```

That means the model can follow the prompt and still fail Pydantic validation.

Fix that first.

## Output Contract

Use one shared JSON object for every rewrite call:

```json
{
  "variants": [
    {
      "kind": "original",
      "target": "keyword",
      "query": "string",
      "why": "short reason",
      "weight": 1.0
    }
  ]
}
```

Allowed `kind` values:

```python
QueryVariantKind = Literal[
    "original",
    "official_docs",
    "community_issues",
    "expanded",
    "focused",
    "entity_a",
    "entity_b",
    "neural_task",
]
```

Allowed `target` values:

```python
QueryTarget = Literal["keyword", "neural", "all"]
```

Rules:

- `keyword` means SearXNG/DDG/Brave/Tavily-style SERP queries.
- `neural` means Gemini/Composio/Jina-style natural-language search tasks.
- `all` is only for original/bypass queries.

## Prompt Builder API

Create `src/kindly_web_search_mcp_server/search/query_rewrite_prompts.py`.

```python
from __future__ import annotations

from typing import Literal

RewriteIntent = Literal["code", "general_research", "comparison"]
ProviderTarget = Literal["keyword", "neural"]


def build_query_rewrite_messages(
    *,
    query: str,
    research_goal: str,
    must_keep_terms: list[str],
    intent: RewriteIntent,
    target: ProviderTarget,
) -> list[dict[str, str]]:
    if target == "neural":
        return [
            {"role": "system", "content": NEURAL_TASK_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_neural_task_user_prompt(
                    query=query,
                    research_goal=research_goal,
                    must_keep_terms=must_keep_terms,
                ),
            },
        ]

    if intent == "comparison":
        system = KEYWORD_COMPARISON_SYSTEM_PROMPT
    elif intent == "general_research":
        system = KEYWORD_GENERAL_SYSTEM_PROMPT
    else:
        system = KEYWORD_CODE_SYSTEM_PROMPT

    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": build_keyword_user_prompt(
                query=query,
                research_goal=research_goal,
                must_keep_terms=must_keep_terms,
                intent=intent,
            ),
        },
    ]
```

## Shared User Prompt Block

Use this exact input block for keyword prompts:

```python
def build_keyword_user_prompt(
    *,
    query: str,
    research_goal: str,
    must_keep_terms: list[str],
    intent: RewriteIntent,
) -> str:
    must_keep = "\n".join(f"- {term}" for term in must_keep_terms) or "- none"
    return f"""RAW_QUERY:
{query}

RESEARCH_GOAL:
{research_goal}

INTENT:
{intent}

MUST_KEEP_TERMS:
{must_keep}

Return JSON only."""
```

Use this exact input block for neural prompts:

```python
def build_neural_task_user_prompt(
    *,
    query: str,
    research_goal: str,
    must_keep_terms: list[str],
) -> str:
    must_keep = "\n".join(f"- {term}" for term in must_keep_terms) or "- none"
    return f"""RAW_AGENT_QUERY:
{query}

RESEARCH_GOAL:
{research_goal}

MUST_KEEP_TERMS:
{must_keep}

Return JSON only."""
```

## Prompt 1: Keyword Code Search

For SearXNG/DDG/Brave/Tavily when intent is `code`.

```python
KEYWORD_CODE_SYSTEM_PROMPT = """You rewrite messy AI-agent web search queries for keyword search engines.

Output JSON only:
{"variants":[{"kind":"original|official_docs|community_issues","target":"keyword","query":"string","why":"string","weight":1.0}]}

Create up to 3 variants:
- original: closest cleaned query
- official_docs: official docs, API reference, changelog, release notes, or spec angle
- community_issues: GitHub issues, discussions, Stack Overflow, forum, workaround, or bug angle

Hard rules:
- Use only these kind values: original, official_docs, community_issues.
- target must always be "keyword".
- Preserve every MUST_KEEP_TERMS item exactly.
- Preserve package names, versions, CLI flags, repo names, APIs, model names, file paths, quoted text, and error fragments.
- Do not invent versions, issue numbers, APIs, products, packages, repos, URLs, or claims.
- Keep each query short enough for a web search box.
- Prefer keyword order over full sentences.
- Do not output duplicate or near-duplicate queries.
- weight must be between 0.8 and 1.2.

Good keyword queries look like:
- fastmcp ResourcesAsTools documentation
- pytest-asyncio event loop closed Windows Python 3.12 GitHub issue
- Gemini Google Search grounding webSearchQueries Python SDK docs

Bad keyword queries look like:
- Please find me the latest information about...
- What are the best ways to...
- fastmcp docs github issue stackoverflow examples resources tools prompts mode code mode guide tutorial"""
```

### Code Few-Shot

Put this in the user prompt before the real input or as an assistant-free example block in the system prompt. Use valid enum values only.

```text
Example input:
RAW_QUERY:
fastmcp resources tools docs prompt as tools code mode

RESEARCH_GOAL:
Find official FastMCP docs and examples for ResourcesAsTools and PromptsAsTools.

MUST_KEEP_TERMS:
- FastMCP
- ResourcesAsTools
- PromptsAsTools

Example output:
{"variants":[
  {"kind":"original","target":"keyword","query":"FastMCP ResourcesAsTools PromptsAsTools CodeMode","why":"Keeps the core terms from the agent query.","weight":1.15},
  {"kind":"official_docs","target":"keyword","query":"FastMCP ResourcesAsTools PromptsAsTools documentation","why":"Targets official docs and API examples.","weight":1.05},
  {"kind":"community_issues","target":"keyword","query":"FastMCP ResourcesAsTools PromptsAsTools GitHub issue discussion","why":"Targets implementation problems and examples.","weight":1.0}
]}
```

## Prompt 2: Keyword General Research

For SearXNG/DDG/Brave/Tavily when intent is `general_research`.

```python
KEYWORD_GENERAL_SYSTEM_PROMPT = """You rewrite messy AI-agent web search queries for general keyword web search.

Output JSON only:
{"variants":[{"kind":"original|expanded|focused","target":"keyword","query":"string","why":"string","weight":1.0}]}

Create up to 3 variants:
- original: closest cleaned query
- expanded: adds missing high-signal terms from RESEARCH_GOAL
- focused: shortest discriminative query

Hard rules:
- Use only these kind values: original, expanded, focused.
- target must always be "keyword".
- Preserve every MUST_KEEP_TERMS item exactly.
- Preserve names, dates, versions, quoted phrases, URLs, identifiers, and codes.
- Do not add docs/GitHub/Stack Overflow bias unless the query is technical.
- Do not invent facts or entities.
- Keep queries concise and searchable.
- weight must be between 0.8 and 1.2.
"""
```

### General Few-Shot

```text
Example input:
RAW_QUERY:
openai browsecomp web search benchmark current limitations

RESEARCH_GOAL:
Understand what BrowseComp measures and why it matters for web-search agents.

MUST_KEEP_TERMS:
- BrowseComp

Example output:
{"variants":[
  {"kind":"original","target":"keyword","query":"OpenAI BrowseComp web search benchmark","why":"Keeps the named benchmark and main topic.","weight":1.15},
  {"kind":"expanded","target":"keyword","query":"OpenAI BrowseComp benchmark web browsing agents limitations","why":"Adds the agent-evaluation context from the research goal.","weight":1.0},
  {"kind":"focused","target":"keyword","query":"BrowseComp benchmark web browsing agents","why":"Shortest discriminative query.","weight":1.05}
]}
```

## Prompt 3: Keyword Comparison

For SearXNG/DDG/Brave/Tavily when intent is `comparison`.

```python
KEYWORD_COMPARISON_SYSTEM_PROMPT = """You rewrite messy AI-agent comparison queries for keyword web search.

Output JSON only:
{"variants":[{"kind":"original|entity_a|entity_b","target":"keyword","query":"string","why":"string","weight":1.0}]}

Create up to 3 variants:
- original: comparison query preserving both entities
- entity_a: query for entity A and the shared comparison aspect
- entity_b: query for entity B and the shared comparison aspect

Hard rules:
- Use only these kind values: original, entity_a, entity_b.
- target must always be "keyword".
- Preserve every MUST_KEEP_TERMS item exactly.
- Entity-specific queries must not mix both entities unless the entity name itself requires it.
- Keep entity_a and entity_b parallel in wording.
- Do not invent comparison dimensions.
- weight must be between 0.8 and 1.2.
"""
```

### Comparison Few-Shot

```text
Example input:
RAW_QUERY:
tavily exa searxng coding agent web search comparison api quality

RESEARCH_GOAL:
Compare Tavily, Exa, and SearXNG as discovery providers for coding agents.

MUST_KEEP_TERMS:
- Tavily
- Exa
- SearXNG

Example output:
{"variants":[
  {"kind":"original","target":"keyword","query":"Tavily Exa SearXNG coding agent web search comparison","why":"Preserves all compared providers and the comparison topic.","weight":1.15},
  {"kind":"entity_a","target":"keyword","query":"Tavily coding agent web search API quality","why":"Isolates Tavily on the shared aspect.","weight":0.95},
  {"kind":"entity_b","target":"keyword","query":"Exa coding agent web search API quality","why":"Isolates Exa on the shared aspect.","weight":0.95}
]}
```

For three-entity comparisons, generate `original` plus the two most important entities unless `max_variants > 3` is explicitly configured. Do not silently create 4+ queries by default.

## Prompt 4: Neural / Grounded Provider Task

For Gemini, Composio LLM Search, and similar providers. This is not a keyword query prompt.

```python
NEURAL_TASK_SYSTEM_PROMPT = """You rewrite messy AI-agent search input into one clear research task for a grounded or neural web-search provider.

Output JSON only:
{"variants":[{"kind":"neural_task","target":"neural","query":"string","why":"string","weight":1.0}]}

Create exactly 1 variant.

Hard rules:
- kind must be "neural_task".
- target must be "neural".
- Write a clear standalone natural-language research task.
- Use RESEARCH_GOAL as the main objective.
- Use RAW_AGENT_QUERY only to recover entities and constraints.
- Preserve every MUST_KEEP_TERMS item exactly.
- Do not write a keyword pile.
- Do not ask for an answer format.
- Do not invent facts, versions, entities, URLs, or claims.
- Include what evidence is desired when RESEARCH_GOAL implies it: official docs, current API examples, issue reports, release notes, benchmarks, or practitioner discussion.
- Keep the task under 35 words unless MUST_KEEP_TERMS force more.
- weight must be 1.0.

Good neural tasks:
- Find official FastMCP documentation and examples for ResourcesAsTools and PromptsAsTools, focusing on Python API usage and limitations.
- Find current Gemini API documentation for Google Search grounding metadata, especially webSearchQueries and groundingChunks behavior in Python.

Bad neural tasks:
- fastmcp ResourcesAsTools docs GitHub issue stackoverflow
- Search the web and provide a comprehensive answer with citations
- What is FastMCP and how can I use it?"""
```

### Neural Few-Shot

```text
Example input:
RAW_AGENT_QUERY:
gemini grounding chunks websearchqueries python sdk docs

RESEARCH_GOAL:
Find current Gemini API Google Search grounding docs and metadata behavior.

MUST_KEEP_TERMS:
- Gemini
- Google Search grounding
- webSearchQueries
- groundingChunks

Example output:
{"variants":[
  {"kind":"neural_task","target":"neural","query":"Find current Gemini API documentation for Google Search grounding metadata, especially webSearchQueries and groundingChunks behavior in Python.","why":"Turns the keyword dump into a provider-friendly grounded research task.","weight":1.0}
]}
```

## Runtime Call Pattern

Do not ask one model call to produce both keyword and neural variants. Use two calls only when both provider groups are active.

```python
keyword_messages = build_query_rewrite_messages(
    query=normalized_query,
    research_goal=research_goal,
    must_keep_terms=policy.must_keep_terms,
    intent=intent,
    target="keyword",
)

neural_messages = build_query_rewrite_messages(
    query=normalized_query,
    research_goal=research_goal,
    must_keep_terms=policy.must_keep_terms,
    intent=intent,
    target="neural",
)
```

If only keyword providers are active, skip the neural prompt.

If only neural providers are active, skip the keyword prompt.

If both are active:

- keyword prompt returns up to 2 keyword variants by default
- neural prompt returns exactly 1 neural task
- total default fanout remains 3

## Validator That Matters

Create `src/kindly_web_search_mcp_server/search/query_rewrite_validate.py`.

```python
from __future__ import annotations

from collections.abc import Iterable

from .normalize import normalize_query
from .query_rewrite_models import QueryVariant


ALLOWED_KINDS_BY_INTENT = {
    "code": {"original", "official_docs", "community_issues"},
    "general_research": {"original", "expanded", "focused"},
    "comparison": {"original", "entity_a", "entity_b"},
}


def validate_keyword_variants(
    variants: Iterable[QueryVariant],
    *,
    intent: str,
    must_keep_terms: list[str],
) -> list[QueryVariant]:
    allowed = ALLOWED_KINDS_BY_INTENT[intent]
    valid: list[QueryVariant] = []
    seen: set[str] = set()

    for variant in variants:
        if variant.target != "keyword":
            continue
        if variant.kind not in allowed:
            continue
        if not _keeps_required_terms(variant.query, must_keep_terms):
            continue
        if _looks_like_prose_answer(variant.query):
            continue
        key = normalize_query(variant.query).casefold()
        if key in seen:
            continue
        seen.add(key)
        valid.append(variant)

    return valid[:3]


def validate_neural_variants(
    variants: Iterable[QueryVariant],
    *,
    must_keep_terms: list[str],
) -> list[QueryVariant]:
    valid: list[QueryVariant] = []
    for variant in variants:
        if variant.target != "neural":
            continue
        if variant.kind != "neural_task":
            continue
        if not _keeps_required_terms(variant.query, must_keep_terms):
            continue
        if _looks_like_keyword_pile(variant.query):
            continue
        valid.append(variant)
    return valid[:1]


def _keeps_required_terms(query: str, must_keep_terms: list[str]) -> bool:
    normalized = normalize_query(query).casefold()
    return all(normalize_query(term).casefold() in normalized for term in must_keep_terms)


def _looks_like_prose_answer(query: str) -> bool:
    lowered = query.strip().casefold()
    return lowered.startswith(("here is", "this query", "the answer", "i need"))


def _looks_like_keyword_pile(query: str) -> bool:
    words = query.split()
    if len(words) < 8:
        return False
    punctuation = query.count(",") + query.count(".")
    return punctuation == 0 and not any(token in query.lower() for token in ("find ", "compare ", "identify ", "verify "))
```

## Orchestrator Wiring

Provider groups:

```python
KEYWORD_PROVIDERS = {"searxng", "ddg", "brave", "tavily"}
NEURAL_PROVIDERS = {"gemini", "composio_llm_search", "jina"}
```

Execution behavior:

```python
for variant in rewrite_plan.execution_variants:
    variant_providers = select_providers_for_target(
        target=variant.target,
        active_provider_names=active_provider_names,
    )
    if not variant_providers:
        continue
    result_sets.append(
        await search_single_query(
            variant.query,
            num_results=per_query_k,
            http_client=client,
            diagnostics=diagnostics,
            providers=variant_providers,
        )
    )
```

Default generated plan for mixed providers:

```json
{
  "execution_variants": [
    {
      "kind": "original",
      "target": "keyword",
      "query": "FastMCP ResourcesAsTools PromptsAsTools CodeMode",
      "weight": 1.15
    },
    {
      "kind": "official_docs",
      "target": "keyword",
      "query": "FastMCP ResourcesAsTools PromptsAsTools documentation",
      "weight": 1.05
    },
    {
      "kind": "neural_task",
      "target": "neural",
      "query": "Find official FastMCP documentation and examples for ResourcesAsTools and PromptsAsTools, focusing on Python API usage and limitations.",
      "weight": 1.0
    }
  ]
}
```

## Minimal Implementation Order

1. Replace invalid current prompt labels with valid labels.
2. Add `target` and `weight` fields.
3. Add `query_rewrite_prompts.py` with the four prompt constants above.
4. Add `query_rewrite_validate.py` with real filtering.
5. Update `rewrite_search_query()` to call keyword and neural prompt builders based on active providers.
6. Update `orchestrator.py` to send keyword variants only to keyword providers and neural variants only to neural providers.
7. Keep `web_search` output unchanged.

## Tests That Matter

Add these focused tests:

```python
def test_keyword_code_prompt_uses_valid_enum_values_only() -> None:
    assert "docs\"" not in KEYWORD_CODE_SYSTEM_PROMPT
    assert "community\"" not in KEYWORD_CODE_SYSTEM_PROMPT
    assert "official_docs" in KEYWORD_CODE_SYSTEM_PROMPT
    assert "community_issues" in KEYWORD_CODE_SYSTEM_PROMPT


def test_neural_prompt_returns_single_neural_task() -> None:
    parsed = QueryRewriteOutput.model_validate({
        "variants": [{
            "kind": "neural_task",
            "target": "neural",
            "query": "Find current Gemini API documentation for Google Search grounding metadata, especially webSearchQueries.",
            "why": "Clear grounded provider task.",
            "weight": 1.0,
        }]
    })
    valid = validate_neural_variants(parsed.variants, must_keep_terms=["Gemini", "webSearchQueries"])
    assert len(valid) == 1


def test_keyword_validator_rejects_neural_target() -> None:
    variant = QueryVariant(
        kind="neural_task",
        target="neural",
        query="Find docs for FastMCP ResourcesAsTools.",
        why="wrong target",
        weight=1.0,
    )
    assert validate_keyword_variants([variant], intent="code", must_keep_terms=[]) == []
```

## What Not To Spend Time On In The First Patch

- No eval harness.
- No new public tool parameter.
- No result-shape change.
- No cache-key redesign.
- No extra metadata contract.
- No broad intent classifier.
- No deep-research orchestration.

The first patch should be prompt correctness plus provider-targeted query generation.
