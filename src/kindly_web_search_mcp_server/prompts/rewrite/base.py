"""XML-tagged query rewrite prompts for GPT-oss workers."""

from __future__ import annotations

from html import escape

from ...search.intents import SearchIntent
from ..builders import anchor_today, join_terms, system_header
from .intents import get_intent_instructions


def build_query_rewrite_prompt(
    *,
    query: str,
    research_goal: str | None,
    intent: SearchIntent,
    must_keep_terms: list[str],
    provider_name: str,
    max_variants: int = 2,
    rewrite_signals: str = "",
) -> tuple[str, str]:
    """Build the two-branch keyword/neural rewrite contract."""
    del provider_name, max_variants
    intent_directives, few_shot_examples = get_intent_instructions(intent)
    system = f"""{system_header()}

<system_instruction>
  <role>You are a world-class search query engineer.</role>
  <task_description>
    Produce exactly two refined variants: keyword_refined for lexical/SERP providers and neural_refined for semantic providers.
    keyword_refined is a concise keyword string. Query terms MUST precede filter operators.
    neural_refined is natural language, contains no site:, filetype:, AND, OR, or NOT operators, and is under 400 characters.
    Both variants must preserve the must_keep_terms and avoid unsupported assumptions.
  </task_description>
  <chain_of_thought>
    <step_1>Identify the core information need.</step_1>
    <step_2>Extract important keywords and entities from the input and signals.</step_2>
    <step_3>Construct keyword_refined with terms before any filters.</step_3>
    <step_4>Construct neural_refined without search operators.</step_4>
    <step_5>Identify terms that must remain in both variants.</step_5>
  </chain_of_thought>
  <temporal_context>Today is {anchor_today()}.</temporal_context>
  <intent_directives>{intent_directives}</intent_directives>
  <search_operators_cookbook>
    site:example.com, filetype:pdf, ext:pdf, intitle:term, inbody:term, lang:es, loc:us,
    +term, -term, \"exact phrase\", uppercase AND/OR/NOT, and freshness hints for news.
  </search_operators_cookbook>
  <output_format>
    Return valid JSON inside <final_response> tags with a variants array containing keyword_refined and neural_refined.
    Each variant must contain kind, target, query, why, weight, branch_type, and must_keep_terms.
  </output_format>
  <intent_few_shot_examples>{few_shot_examples}</intent_few_shot_examples>
</system_instruction>"""
    user = f"""<user_input>
  <raw_query>{escape(query)}</raw_query>
  <research_goal>{escape(research_goal or query)}</research_goal>
  <intent>{escape(str(intent))}</intent>
  <must_keep_terms>{escape(join_terms(must_keep_terms))}</must_keep_terms>
  <rewrite_signals>
{rewrite_signals}
  </rewrite_signals>
</user_input>
Return the final JSON inside <final_response>...</final_response> tags."""
    return system, user
