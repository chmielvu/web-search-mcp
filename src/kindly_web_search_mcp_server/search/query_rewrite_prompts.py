from __future__ import annotations

from .query_rewrite_models import RewriteIntent

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
- community_issues queries must include at least one of: GitHub, issue, discussion, Stack Overflow, forum, workaround, bug.
- Keep each query short enough for a web search box.
- Prefer keyword order over full sentences.
- Do not output duplicate or near-duplicate queries.
- why must not be empty.
- weight must be between 0.8 and 1.2.

Example output:
{"variants":[
  {"kind":"original","target":"keyword","query":"FastMCP ResourcesAsTools PromptsAsTools CodeMode","why":"Keeps the core terms from the agent query.","weight":1.15},
  {"kind":"official_docs","target":"keyword","query":"FastMCP ResourcesAsTools PromptsAsTools documentation","why":"Targets official docs and API examples.","weight":1.05},
  {"kind":"community_issues","target":"keyword","query":"FastMCP ResourcesAsTools PromptsAsTools GitHub issue discussion","why":"Targets community debugging and implementation examples.","weight":1.0}
]}"""

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
- why must not be empty.
- weight must be between 0.8 and 1.2.

Example output:
{"variants":[
  {"kind":"original","target":"keyword","query":"OpenAI BrowseComp web search benchmark","why":"Keeps the named benchmark and main topic.","weight":1.15},
  {"kind":"expanded","target":"keyword","query":"OpenAI BrowseComp benchmark web browsing agents limitations","why":"Adds the agent-evaluation context from the research goal.","weight":1.0},
  {"kind":"focused","target":"keyword","query":"BrowseComp benchmark web browsing agents","why":"Shortest discriminative query.","weight":1.05}
]}"""

KEYWORD_COMPARISON_SYSTEM_PROMPT = """You rewrite messy AI-agent comparison queries for keyword web search.

Output JSON only:
{"variants":[{"kind":"original|entity_a|entity_b","target":"keyword","query":"string","why":"string","weight":1.0}]}

Create up to 3 variants:
- original: comparison query preserving both or all named entities
- entity_a: query for exactly one named entity and the shared comparison aspect
- entity_b: query for exactly one different named entity and the shared comparison aspect

Hard rules:
- Use only these kind values: original, entity_a, entity_b.
- target must always be "keyword".
- Preserve every MUST_KEEP_TERMS item exactly across the whole variant set.
- entity_a and entity_b must each contain exactly one compared entity name.
- entity_a and entity_b must use different entities.
- entity-specific queries must not contain any second compared entity.
- Keep entity_a and entity_b parallel in wording.
- Do not invent comparison dimensions.
- why must not be empty.
- weight must be between 0.8 and 1.2.

Example output:
{"variants":[
  {"kind":"original","target":"keyword","query":"Tavily Exa SearXNG coding agent web search comparison","why":"Preserves all compared providers and the comparison topic.","weight":1.15},
  {"kind":"entity_a","target":"keyword","query":"Tavily coding agent web search API quality","why":"Isolates Tavily on the shared aspect.","weight":0.95},
  {"kind":"entity_b","target":"keyword","query":"Exa coding agent web search API quality","why":"Isolates Exa on the shared aspect.","weight":0.95}
]}"""

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
- why must not be empty.
- Keep the task under 35 words unless MUST_KEEP_TERMS force more.
- weight must be 1.0.

Example output:
{"variants":[
  {"kind":"neural_task","target":"neural","query":"Find current Gemini API documentation for Google Search grounding metadata, especially webSearchQueries and groundingChunks behavior in Python.","why":"Turns the keyword dump into a provider-friendly grounded research task.","weight":1.0}
]}"""


def build_query_rewrite_messages(
    *,
    query: str,
    research_goal: str | None,
    must_keep_terms: list[str],
    intent: RewriteIntent,
    target: str,
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


def build_keyword_user_prompt(
    *,
    query: str,
    research_goal: str | None,
    must_keep_terms: list[str],
    intent: RewriteIntent,
) -> str:
    must_keep = "\n".join(f"- {term}" for term in must_keep_terms) or "- none"
    goal = research_goal or query
    return f"""RAW_QUERY:
{query}

RESEARCH_GOAL:
{goal}

INTENT:
{intent}

MUST_KEEP_TERMS:
{must_keep}

Return JSON only."""


def build_neural_task_user_prompt(
    *,
    query: str,
    research_goal: str | None,
    must_keep_terms: list[str],
) -> str:
    must_keep = "\n".join(f"- {term}" for term in must_keep_terms) or "- none"
    goal = research_goal or query
    return f"""RAW_AGENT_QUERY:
{query}

RESEARCH_GOAL:
{goal}

MUST_KEEP_TERMS:
{must_keep}

Return JSON only."""
