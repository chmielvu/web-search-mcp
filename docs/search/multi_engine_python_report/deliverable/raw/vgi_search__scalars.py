"""``web_answer`` -- a synthesized one-line answer for a query (scalar).

``web_answer(query, provider)`` returns a single VARCHAR: a synthesized answer
from a provider that exposes one (``tavily`` with a key, or ``ddg`` Instant
Answer for free). For a provider without an answer, or a query with none, it
returns NULL -- never an error, never a crash.

Scalars are POSITIONAL-ONLY in VGI/DuckDB (``name := value`` is a table-function
feature), so ``provider`` is a positional :class:`ConstParam`, not a named arg::

    SELECT web_answer('who maintains duckdb', 'tavily');
    SELECT web_answer('python programming language', 'ddg');

The ``tavily`` key is supplied via the VGI secret provider (declared as an
optional :class:`Secret` so a missing key degrades to NULL rather than failing
the whole scan); ``ddg`` needs no key. Keys are never read from SQL.
"""

from __future__ import annotations

from typing import Annotated, Any

import pyarrow as pa
from vgi.arguments import ConstParam, Param, Returns, Secret
from vgi.metadata import FunctionExample
from vgi.scalar_function import ScalarFunction

from vgi_search.meta import example_queries_json, object_tags
from vgi_search.providers import ProviderError, build_provider
from vgi_search.secrets import key_from_secret

_WEB_ANSWER_DOC_LLM = (
    "Return a single synthesized one-line answer for a natural-language question, as a scalar "
    "`VARCHAR`.\n\n"
    "`web_answer(query, provider)` asks a provider that exposes a *zero-click* / synthesized "
    "answer -- `tavily` (with an API key) or `ddg` (DuckDuckGo Instant Answer, free, no key) -- "
    "and returns the answer text, or `NULL` when the provider has no answer for the query, is "
    "unknown/disabled, or has no key. It NEVER raises: a provider failure degrades to `NULL` so it "
    "is safe to use inside a larger scan.\n\n"
    "**Use it when** you want a quick factual snippet to ground a prompt and do not need ranked "
    "result rows; for the full list of results use `web_search` instead.\n\n"
    "**Inputs:** `query` (the question, one per row) and `provider` (a positional constant: "
    "`'tavily'` or `'ddg'`). **Output:** one `VARCHAR` per input row (the answer or `NULL`). "
    "**Edge cases:** queries with no instant answer return `NULL`; the `tavily` key is read from "
    "the VGI secret provider, never from SQL; a `NULL` input query yields `NULL`."
)

_WEB_ANSWER_DOC_MD = (
    "# web_answer\n\n"
    "A scalar that returns a **synthesized one-line answer** for a question, or `NULL` when none "
    "is available.\n\n"
    "## Usage\n\n"
    "```sql\n"
    "SELECT search.main.web_answer('python programming language', 'ddg');\n"
    "SELECT search.main.web_answer('who maintains duckdb', 'tavily');\n"
    "```\n\n"
    "The second argument is the provider name, a positional constant (scalars are positional-only "
    "in VGI/DuckDB). Use `'ddg'` for a free DuckDuckGo Instant Answer or `'tavily'` for a keyed, "
    "richer synthesis.\n\n"
    "## Notes\n\n"
    "- Returns `NULL` -- never an error -- when the provider has no answer, is unknown/disabled, or "
    "is missing its key, so it is safe inside a wider query.\n"
    "- The Tavily key comes from the VGI secret provider; keys are never passed in SQL.\n"
    "- For ranked result rows (title/url/snippet/...) use the `web_search` table function."
)

_WEB_ANSWER_EXAMPLES = [
    FunctionExample(
        sql="SELECT search.main.web_answer('python programming language', 'ddg')",
        description="DuckDuckGo Instant Answer (free, no key)",
    ),
    FunctionExample(
        sql="SELECT search.main.web_answer('who maintains duckdb', 'tavily')",
        description="Tavily-synthesized answer (needs a key)",
    ),
]


class WebAnswer(ScalarFunction):
    """``web_answer(query, provider)`` -> synthesized answer string (or NULL)."""

    class Meta:
        """Function metadata."""

        name = "web_answer"
        description = "Synthesized one-line answer for a query (tavily/ddg); NULL when unavailable"
        categories = ["search", "web", "rag"]
        required_secrets = ["tavily"]
        tags = {  # noqa: RUF012 - declarative metadata, not mutated
            **object_tags(
                title="Synthesized Web Answer",
                doc_llm=_WEB_ANSWER_DOC_LLM,
                doc_md=_WEB_ANSWER_DOC_MD,
                keywords=[
                    "web answer",
                    "instant answer",
                    "synthesized answer",
                    "zero-click",
                    "question answering",
                    "qa",
                    "ddg",
                    "duckduckgo",
                    "tavily",
                    "rag",
                    "snippet",
                    "fact lookup",
                ],
            ),
            "vgi.category": "Web Search",
            # VGI515: DuckDB's native duckdb_functions().examples carrier drops
            # each example's description, so publish the same examples (with
            # descriptions) as a vgi.example_queries JSON list. Built from the
            # same _WEB_ANSWER_EXAMPLES list so the two stay byte-identical.
            "vgi.example_queries": example_queries_json(_WEB_ANSWER_EXAMPLES),
        }
        examples = _WEB_ANSWER_EXAMPLES

    @classmethod
    def compute(
        cls,
        query: Annotated[pa.StringArray, Param(doc="Query to answer.")],
        provider: Annotated[str, ConstParam("Provider name: 'tavily' or 'ddg'.")],
        tavily_secret: Annotated[dict[str, pa.Scalar[Any]] | None, Secret("tavily")] = None,
    ) -> Annotated[pa.StringArray, Returns()]:
        """Return a synthesized answer per query row (NULL when unavailable)."""
        # Resolve a key from the (optional) secret material; env fallback is
        # applied inside build_provider.
        secrets: dict[str, Any] = {"tavily": tavily_secret} if tavily_secret else {}
        api_key = key_from_secret(secrets, provider)

        try:
            backend = build_provider(provider, api_key=api_key)
        except ProviderError:
            # Unknown/disabled provider -> NULL for every row (never crash).
            return pa.array([None] * len(query), type=pa.string())

        if not getattr(backend, "supports_answer", False):
            return pa.array([None] * len(query), type=pa.string())

        out: list[str | None] = []
        for value in query.to_pylist():
            if value is None:
                out.append(None)
                continue
            try:
                out.append(backend.answer(value, opts={}))
            except ProviderError:
                out.append(None)
            except Exception:  # noqa: BLE001 - answers must never crash a scan
                out.append(None)
        return pa.array(out, type=pa.string())


SCALAR_FUNCTIONS: list[type] = [WebAnswer]
