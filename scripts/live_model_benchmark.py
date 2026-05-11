from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.panel import Panel
from rich.table import Table
from rich.markup import escape
from typing import cast

console = Console()

SYSTEM_PROMPT = (
    """<role>You are a research analyst with access to Google Search grounding.</role>

<constraints>
1. Be objective and factual.
2. Cite all sources inline using [N] notation.
3. Mark uncertainty clearly.
4. No speculation without sources.
</constraints>

<task>
Given a query:
1. Plan research strategy
2. Execute searches via Google Search grounding
3. Cross-reference across sources
4. Synthesize into structured report

Output:
- Executive summary first
- Key findings with inline [N] citations
- Sources section at end
- Mark report after "---" line
</task>"""
)
POLLI_ENDPOINT = "https://gen.pollinations.ai/v1/chat/completions"
MAX_RESPONSE_CHARS = 3000
MAX_JUDGE_RESPONSE_CHARS = 1200
MAX_SOURCES_PER_RESPONSE = 10
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 30.0
DISPATCH_INTERVAL_S = 10.0
JUDGE_MATCH_DELAY_S = 20.0
DEFAULT_MARKDOWN_REPORT = "benchmark_report.md"


class PromptSpec(TypedDict):
    key: str
    title: str
    text: str
    markers: tuple[str, ...]


class RunnerSpec(TypedDict):
    kind: str
    label: str
    target: str


class RunResult(TypedDict):
    runner: str
    prompt: str
    prompt_key: str
    ok: bool
    elapsed_s: float
    answer: str
    sources: list[str]
    error: str
    error_type: str
    marker_hits: int
    word_count: int


@dataclass
class BenchmarkState:
    results: list[RunResult | None] = field(default_factory=list)
    start_time: float = 0.0
    completed_count: int = 0
    error_count: int = 0
    total_latency: float = 0.0


PROMPTS: list[PromptSpec] = [
    # --- Frameworks & Composition (Search Soup) ---
    {
        "key": "agent_framework_comparison",
        "title": "Agent Orchestration Comparison",
        "text": "langchain agent llm orchestration rag workflow difference llamaindex 2026",
        "markers": ("langchain", "llamaindex", "rag"),
        # Baseline: "LangChain (and LangGraph) focuses on general agentic control flows and deep custom orchestration. LlamaIndex is heavily optimized for data ingestion, indexing, and RAG pipelines out-of-the-box."
    },
    {
        "key": "nextjs_turbopack",
        "title": "Next.js 15 Turbopack",
        "text": "nextjs 15 turbopack react 19 server actions setup requirements command",
        "markers": ("--turbo", "next.config"),
        # Baseline: "To use Turbopack in Next.js 15 with React 19 server actions, you run `next dev --turbo`. Server actions are supported natively in Next.js 15 App Router without extra configuration."
    },
    {
        "key": "multi_agent_benchmarks",
        "title": "Multi-Agent Frameworks",
        "text": "crewai vs autogen multi agent framework performance speed reliability",
        "markers": ("crewai", "autogen"),
        # Baseline: "CrewAI uses a role-based, process-driven design (often sequential or hierarchical). AutoGen uses conversational agents communicating via messages. AutoGen can be more complex to wire up but offers highly flexible code-execution loops."
    },

    # --- Recency & API Deprecations ---
    {
        "key": "pydantic_v2_settings",
        "title": "Pydantic v2 Settings Migration",
        "text": "pydantic v2 BaseSettings import path package name code",
        "markers": ("pydantic-settings", "pydantic_settings"),
        # Baseline: "In Pydantic v2, BaseSettings was extracted to a separate package. Install it via `pip install pydantic-settings` and import it using `from pydantic_settings import BaseSettings`."
    },
    {
        "key": "openai_sdk_v1_stream",
        "title": "OpenAI Python SDK v1 Streaming",
        "text": "openai python sdk v1 streaming chat completion snippet syntax async",
        "markers": ("client.chat.completions.create", "stream=True"),
        # Baseline: "Using the v1 SDK: `client = AsyncOpenAI(); response = await client.chat.completions.create(model='gpt-4o', messages=[...], stream=True); async for chunk in response: print(chunk.choices[0].delta.content)`."
    },
    {
        "key": "hf_flash_attn2",
        "title": "HuggingFace Flash Attention 2",
        "text": "huggingface transformers load model pipeline flash attention 2 kwarg lora",
        "markers": ("attn_implementation", "flash_attention_2"),
        # Baseline: "When loading a model via `AutoModelForCausalLM.from_pretrained()`, pass the kwarg `attn_implementation='flash_attention_2'`."
    },

    # --- Tricky & Noise Filtering (Hallucinations) ---
    {
        "key": "claude_context_window",
        "title": "Claude 3.5 Sonnet Context Limit",
        "text": "anthropic claude 3.5 sonnet context window max token limit size",
        "markers": ("200,000", "200k"),
        # Baseline: "Claude 3.5 Sonnet supports a maximum context window of 200,000 (200k) tokens."
    },
    {
        "key": "python_313_gil",
        "title": "Python 3.13 GIL Optional",
        "text": "python 3.13 GIL completely removed disabled default build flag PEP 703",
        "markers": ("--disable-gil", "optional"),
        # Baseline: "The GIL is NOT removed by default in Python 3.13. It is an experimental opt-in feature via PEP 703, requiring python to be compiled with `--disable-gil`."
    },
    {
        "key": "react_use_hook",
        "title": "React 19 use Hook",
        "text": "react 19 new use hook fetch promise suspend difference useEffect",
        "markers": ("use(", "suspense"),
        # Baseline: "The `use()` hook lets you read the value of a Promise or Context synchronously within a component, integrating natively with Suspense. Unlike `useEffect`, it can be called conditionally and pauses rendering until the promise resolves."
    },

    # --- Deep Technical & Math/ML ---
    {
        "key": "lora_vram_diff",
        "title": "LoRA vs QLoRA VRAM",
        "text": "7B parameter LLM fine tuning VRAM requirement diff standard LoRA fp16 vs QLoRA 4-bit normal float batch size 1",
        "markers": ("GB", "gigabytes"),
        # Baseline: "A 7B model in fp16 takes ~14GB just for weights, plus gradients and optimizer states, often requiring >24GB VRAM for standard LoRA. QLoRA (4-bit) reduces the base weight footprint to ~4GB, allowing tuning on ~10-12GB VRAM GPUs (like an RTX 3060)."
    },
    {
        "key": "pytorch_compile_memory",
        "title": "PyTorch 2.x Compile Leak",
        "text": "pytorch 2.2 torch.compile memory leak workaround windows fix",
        "markers": ("torch._dynamo", "cache_size_limit"),
        # Baseline: "Repeated recompilations (cache misses) via `torch.compile` can cause memory bloat. Workarounds include increasing `torch._dynamo.config.cache_size_limit`, passing static shapes, or ensuring graph breaks aren't happening dynamically."
    },
    
    # --- MCP Specific / Meta Context ---
    {
        "key": "mcp_transports",
        "title": "MCP Transport Protocols",
        "text": "github copilot mcp server integration stdio vs sse differences webhooks",
        "markers": ("stdio", "sse", "server-sent events"),
        # Baseline: "MCP uses stdio for local command-line processes (exchanging length-prefixed JSONRPC messages via standard input/output). SSE (Server-Sent Events) is used for remote servers over HTTP, sending events one-way with POST requests for replies."
    },
    
    # --- Preserved Original Baselines ---
    {
        "key": "fastmcp_latest",
        "title": "FastMCP latest stable version",
        "text": "fastmcp latest stable release version {today} date URL",
        "markers": ("3.2.4", "FastMCP"),
        # Baseline: "Provides the correct recent FastMCP version release string and link (depending on exact run date)."
    },
    {
        "key": "mistral_import",
        "title": "Mistral import-time failure mitigation",
        "text": "python mistralai.client.Mistral missing mismatched import-time failure lazy import fallback architecture",
        "markers": ("lazy", "import"),
        # Baseline: "Use a lazy import technique inside a method or function (e.g., `import mistralai` only when the class is instantiated) instead of top-level imports to ensure the module doesn't fail at startup if the dependency is absent."
    },
    {
        "key": "karol_nawrocki",
        "title": "Karol Nawrocki current status",
        "text": "Karol Nawrocki current role status polish politics {today}",
        "markers": ("President", "2025"),
        # Baseline: "He is the current President of Poland (won the late 2025 election and inaugurated)."
    },
]
POLLINATIONS_MODELS = [
    ("polly", "Pollinations / polly"),
    ("gemini-search", "Pollinations / gemini-search"),
    ("gemini-flash-lite-3.1", "Pollinations / gemini-flash-lite-3.1"),
]


def truncate(text: str, limit: int = 140) -> str:
    text = " ".join(text.strip().split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def truncate_response(text: str, limit: int = MAX_RESPONSE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text)} chars total]"


def write_text_file(filepath: str, content: str) -> None:
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def extract_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s<>\]\)\"']+", text)
    return list(dict.fromkeys(urls))


def today_str() -> str:
    return time.strftime("%Y-%m-%d")


def format_prompt_text(prompt_text: str) -> str:
    return prompt_text.format(today=today_str())


def load_keys(repo_root: Path) -> tuple[str, str]:
    load_dotenv(repo_root / ".env")
    load_dotenv()
    pollinations_key = os.environ.get("POLLINATIONS_API_KEY", "").strip()
    gemini_key = os.environ.get("KINDLY_GEMINI_API_KEY", "").strip()
    if not pollinations_key:
        raise SystemExit("POLLINATIONS_API_KEY is required in the repo env.")
    if not gemini_key:
        raise SystemExit("KINDLY_GEMINI_API_KEY is required in the repo env.")
    return pollinations_key, gemini_key


async def discover_gemini_models(
    api_key: str, client: httpx.AsyncClient
) -> dict[str, str]:
    response = await client.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": api_key},
    )
    response.raise_for_status()
    found: dict[str, str] = {}
    for model in response.json().get("models", []):
        name = str(model.get("name", ""))
        suffix = name.removeprefix("models/")
        for needle in ("gemma-4-31b-it", "gemma-4-26b-a4b-it"):
            if needle in suffix and needle not in found:
                found[needle] = name
    return found


def build_runners(gemini_ids: dict[str, str]) -> list[RunnerSpec]:
    runners: list[RunnerSpec] = [
        {"kind": "pollinations", "label": label, "target": target}
        for target, label in POLLINATIONS_MODELS
    ]
    for suffix, label in (
        ("gemma-4-31b-it", "Gemini API / gemma-4-31b-it"),
        ("gemma-4-26b-a4b-it", "Gemini API / gemma-4-26b-a4b-it"),
    ):
        actual = gemini_ids.get(suffix)
        if actual:
            runners.append({"kind": "gemini", "label": label, "target": actual})
    return runners


def classify_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 429:
            return "rate_limit"
        if 500 <= exc.response.status_code < 600:
            return "server_error"
        return "http_error"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connection"
    return "unknown"


async def retry_with_backoff(
    coro_factory,
    max_attempts: int = RETRY_MAX_ATTEMPTS,
    base_delay: float = RETRY_BASE_DELAY,
    max_delay: float = RETRY_MAX_DELAY,
) -> tuple[bool, object, str]:
    last_error = ""
    for attempt in range(max_attempts):
        try:
            result = await coro_factory()
            return True, result, ""
        except Exception as exc:
            error_type = classify_error(exc)
            last_error = f"{error_type}: {str(exc)[:100]}"
            if error_type in ("rate_limit", "server_error", "timeout", "connection"):
                if attempt < max_attempts - 1:
                    delay = min(
                        base_delay * (2**attempt) + random.uniform(0, 1), max_delay
                    )
                    await asyncio.sleep(delay)
            else:
                break
    return False, None, last_error


async def pollinations_answer(
    client: httpx.AsyncClient, api_key: str, model: str, prompt: str
) -> tuple[str, list[str]]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "stream": False,
    }
    response = await client.post(
        POLLI_ENDPOINT, json=payload, headers={"Authorization": f"Bearer {api_key}"}
    )
    response.raise_for_status()
    data = response.json()
    answer = str(
        data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
    )
    sources = [
        str(item) for item in (data.get("citations", []) or extract_urls(answer))
    ]
    return answer, sources[:MAX_SOURCES_PER_RESPONSE]


async def gemini_answer(
    client: genai.Client, model_name: str, prompt: str
) -> tuple[str, list[str]]:
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.2,
    )
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=model_name.removeprefix("models/"),
        contents=prompt,
        config=config,
    )
    answer = getattr(response, "text", "") or ""
    sources: list[str] = []
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        metadata = getattr(candidates[0], "grounding_metadata", None)
        if metadata:
            for chunk in getattr(metadata, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                url = getattr(web, "uri", None) if web else None
                if url and url not in sources:
                    sources.append(url)
    return answer, sources[:MAX_SOURCES_PER_RESPONSE] or extract_urls(answer)[
        :MAX_SOURCES_PER_RESPONSE
    ]


async def run_one(
    runner: RunnerSpec,
    prompt: PromptSpec,
    http_client: httpx.AsyncClient,
    *,
    pollinations_key: str,
    gemini_client: genai.Client,
) -> RunResult:
    start = time.perf_counter()
    prompt_text = format_prompt_text(prompt["text"])
    ok = False
    answer = ""
    sources: list[str] = []
    error = ""
    error_type = ""

    if runner["kind"] == "pollinations":
        success, result, err_msg = await retry_with_backoff(
            lambda: pollinations_answer(
                http_client, pollinations_key, runner["target"], prompt_text
            )
        )
        if success and result:
            typed_result = cast(tuple[str, list[str]], result)
            answer, sources = typed_result
            ok = True
        else:
            error = err_msg
            error_type = err_msg.split(":")[0] if ":" in err_msg else "unknown"
    else:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.2,
        )
        success, response_obj, err_msg = await retry_with_backoff(
            lambda: asyncio.to_thread(
                gemini_client.models.generate_content,
                model=runner["target"].removeprefix("models/"),
                contents=prompt_text,
                config=config,
            )
        )
        if success and response_obj is not None:
            answer = getattr(response_obj, "text", "") or ""
            sources: list[str] = []
            candidates = getattr(response_obj, "candidates", None) or []
            if candidates:
                metadata = getattr(candidates[0], "grounding_metadata", None)
                if metadata:
                    for chunk in getattr(metadata, "grounding_chunks", None) or []:
                        web = getattr(chunk, "web", None)
                        url = getattr(web, "uri", None) if web else None
                        if url and url not in sources:
                            sources.append(url)
            ok = True
            sources = sources[:MAX_SOURCES_PER_RESPONSE]
        else:
            ok = False
            answer = ""
            sources = []
            error = err_msg
            error_type = err_msg.split(":")[0] if ":" in err_msg else "unknown"

    elapsed = time.perf_counter() - start
    return {
        "runner": runner["label"],
        "prompt": prompt["title"],
        "prompt_key": prompt["key"],
        "ok": ok,
        "elapsed_s": elapsed,
        "answer": answer,
        "sources": sources,
        "error": error,
        "error_type": error_type,
        "marker_hits": sum(
            1 for marker in prompt["markers"] if marker.lower() in answer.lower()
        ),
        "word_count": len(answer.split()),
    }


def build_failed_result(
    runner: RunnerSpec,
    prompt: PromptSpec,
    error: Exception,
    elapsed_s: float,
) -> RunResult:
    error_type = classify_error(error)
    return {
        "runner": runner["label"],
        "prompt": prompt["title"],
        "prompt_key": prompt["key"],
        "ok": False,
        "elapsed_s": elapsed_s,
        "answer": "",
        "sources": [],
        "error": f"{error_type}: {str(error)[:300]}",
        "error_type": error_type,
        "marker_hits": 0,
        "word_count": 0,
    }


def build_json_payload(
    gemini_ids: dict[str, str],
    results: list[RunResult | None],
    judgment_text: str,
) -> dict[str, object]:
    completed = [item for item in results if item is not None]
    return {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pollinations_endpoint": POLLI_ENDPOINT,
        "gemini_ids": gemini_ids,
        "results": completed,
        "llm_judge_analysis": judgment_text if judgment_text else None,
    }


def choose_pairwise_winner_fallback(
    model_a: str,
    results_a: list[RunResult],
    model_b: str,
    results_b: list[RunResult],
) -> str:
    def metrics(results: list[RunResult]) -> tuple[int, int, float]:
        ok_count = sum(1 for item in results if item.get("ok"))
        marker_hits = sum(int(item.get("marker_hits", 0)) for item in results)
        latencies = [float(item.get("elapsed_s", 0.0)) for item in results if item.get("ok")]
        avg_latency = statistics.fmean(latencies) if latencies else float("inf")
        return ok_count, marker_hits, -avg_latency

    return model_a if metrics(results_a) >= metrics(results_b) else model_b


LLM_JUDGE_PROMPT = """You are an expert LLM judge evaluating AI assistant responses for a comparative benchmark. Apply chain-of-thought reasoning to compare exactly TWO models across multiple queries.

## Benchmark Questions
{questions}

## Model A Responses
{model_a_responses}

## Model B Responses
{model_b_responses}

## Evaluation Process
1. **Analyze performance on each query**: Compare Model A and Model B directly on accuracy, completeness, citation quality, and clarity.
2. **Determine an overall winner**: Between Model A and Model B, which one was superior across the 15 queries?
3. **Required Output Format**:
   - Give detailed reasoning for the final decision.
   - Explicitly declare the winner on the VERY LAST LINE in exactly this format: "WINNER: <model_name>"
"""

async def llm_judge_evaluate_pairwise(
    gemini_client: genai.Client,
    model_a: str,
    results_a: list[RunResult],
    model_b: str,
    results_b: list[RunResult],
    prompts: list[PromptSpec],
) -> tuple[str, str]:
    questions_section = ""
    for p in sorted(prompts, key=lambda x: x["key"]):
        formatted_text = format_prompt_text(p["text"])
        questions_section += f"### {p['key']}: {p['title']}\n- {formatted_text}\n\n"

    def build_res(results, model_name):
        responses_section = f"\n## Model: {model_name}\n"
        for r in sorted(results, key=lambda x: x["prompt_key"]):
            responses_section += f"\n### Response to: {r.get('prompt_key')}\n"
            if r.get("ok"):
                answer = truncate_response(
                    r.get("answer", ""), MAX_JUDGE_RESPONSE_CHARS
                )
                responses_section += f"{answer}\n"
                sources = r.get("sources", [])
                if sources:
                    responses_section += f"Sources: {', '.join(str(s) for s in sources[:MAX_SOURCES_PER_RESPONSE])}\n"
                responses_section += (
                    f"Elapsed: {r.get('elapsed_s', 0):.2f}s | "
                    f"Words: {r.get('word_count', 0)}\n"
                )
            else:
                responses_section += "[FAILED]\n"
                responses_section += f"Error: {r.get('error_type', 'unknown')}\n"
        return responses_section

    formatted_prompt = LLM_JUDGE_PROMPT.format(
        questions=questions_section,
        model_a_responses=build_res(results_a, model_a),
        model_b_responses=build_res(results_b, model_b),
    )

    config = types.GenerateContentConfig(temperature=0.1)
    success, response_obj, err_msg = await retry_with_backoff(
        lambda: asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-flash-latest".removeprefix("models/"),
            contents=formatted_prompt,
            config=config,
        )
    )
    content = ""
    winner = ""
    if success and response_obj is not None:
        content = getattr(response_obj, "text", "") or ""
        lines = content.strip().split("\n")
        for line in reversed(lines):
            if line.startswith("WINNER:"):
                winner = line.split("WINNER:")[1].strip()
                break
    else:
        content = f"❌ LLM Judge evaluation failed: {err_msg}"
    
    if not winner:
        winner = choose_pairwise_winner_fallback(
            model_a, results_a, model_b, results_b
        )
        content += (
            "\n\n[FALLBACK] Missing explicit winner line; selected by local benchmark metrics: "
            f"{winner}"
        )

    return winner, content


async def run_judgments(
    results: list[RunResult],
    prompts: list[PromptSpec],
    gemini_client: genai.Client,
    console: Console,
) -> str:
    grouped_by_model: dict[str, list[RunResult]] = {}
    for r in sorted(results, key=lambda x: (x["runner"], x["prompt_key"])):
        runner_str = str(r["runner"])
        grouped_by_model.setdefault(runner_str, []).append(r)
    
    models = list(grouped_by_model.keys())
    if len(models) < 2:
        return "Not enough models to run a pairwise tournament."
    
    current_champion = models[0]
    full_evaluation_log = f"=== Pairwise Evaluation Tournament ===\nInitial Champion: {current_champion}\n\n"
    
    with console.status(
        "[bold cyan]⚖️ Running LLM-as-a-Judge Pairwise Tournament (20s delay between calls)...[/bold cyan]"
    ):
        for challenger in models[1:]:
            console.print(f"[cyan]Evaluating: {current_champion} vs {challenger}...[/cyan]")
            
            winner, match_eval = await llm_judge_evaluate_pairwise(
                gemini_client,
                current_champion,
                grouped_by_model[current_champion],
                challenger,
                grouped_by_model[challenger],
                prompts
            )
            
            full_evaluation_log += f"--- Match: {current_champion} vs {challenger} ---\n"
            full_evaluation_log += match_eval + "\n\n"
            full_evaluation_log += f"Match Winner: {winner}\n\n"
            
            # Ensure winner is exactly one of the two to prevent hallucinated names
            if winner not in [current_champion, challenger]:
                # Attempt loose matching
                if current_champion in winner:
                    current_champion = current_champion
                elif challenger in winner:
                    current_champion = challenger
            else:
                current_champion = winner
            
            if challenger != models[-1]:
                console.print(f"[dim]Waiting 20 seconds before next evaluation to avoid rate limits...[/dim]")
                await asyncio.sleep(JUDGE_MATCH_DELAY_S)

    full_evaluation_log += f"=== GRAND CHAMPION: {current_champion} ==="
    return full_evaluation_log


def save_markdown_report(
    results: list[RunResult | None],
    prompts: list[PromptSpec],
    runners: list[RunnerSpec],
    judgment_text: str,
    filepath: str,
) -> None:
    lines = ["# Web Search LLM Benchmark Report\n"]
    lines.append(f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

    if judgment_text:
        lines.append("## LLM Judge Evaluation\n")
        lines.append(f"{judgment_text}\n\n")

    lines.append("## Detailed Answers by Query\n")

    for p_index, prompt in enumerate(prompts):
        lines.append(f"### {prompt['title']} (`{prompt['key']}`)")
        lines.append(f"**Query:** `{prompt['text']}`\n")

        for r_index, runner in enumerate(runners):
            idx = p_index * len(runners) + r_index
            item = results[idx]
            lines.append(f"#### Model: {runner['label']}")

            if item is None:
                lines.append("*Did not complete.*\n")
                continue

            if item.get("ok"):
                lines.append(
                    f"**Time:** {item.get('elapsed_s', 0):.2f}s | "
                    f"**Words:** {item.get('word_count', 0)} | "
                    f"**Sources:** {len(item.get('sources', []))}\n"
                )
                lines.append(f"{item.get('answer', '')}\n")
                if item.get("sources"):
                    lines.append("\n**Sources:**")
                    for s in item.get("sources", []):
                        lines.append(f"- {s}")
                lines.append("\n")
            else:
                lines.append(f"**FAILED:** {item.get('error_type', 'unknown')}\n")
                lines.append(f"```\n{item.get('error', '')}\n```\n")
        lines.append("---\n")

    write_text_file(filepath, "\n".join(lines))


def save_json_report(payload: dict[str, object], filepath: str) -> None:
    write_text_file(filepath, json.dumps(payload, indent=2, ensure_ascii=False))


def render_summary_table(results: list[RunResult]) -> Table:
    grouped: dict[str, list[RunResult]] = {}
    for result in results:
        grouped.setdefault(str(result["runner"]), []).append(result)

    summary_data: list[tuple[str, dict[str, float | int]]] = []
    for name in sorted(grouped.keys()):
        items = grouped[name]
        ok_items = [item for item in items if item.get("ok")]
        elapsed = [float(item["elapsed_s"]) for item in ok_items]
        word_counts = [int(item["word_count"]) for item in ok_items]
        marker_hits = [int(item["marker_hits"]) for item in ok_items]
        summary_data.append(
            (
                name,
                {
                    "runs": len(items),
                    "ok": sum(1 for item in items if item.get("ok")),
                    "avg_s": round(statistics.fmean(elapsed), 2) if elapsed else 0.0,
                    "p50_s": round(statistics.median(elapsed), 2) if elapsed else 0.0,
                    "max_s": round(max(elapsed), 2) if elapsed else 0.0,
                    "avg_words": round(statistics.fmean(word_counts), 1)
                    if word_counts
                    else 0.0,
                    "avg_markers": round(statistics.fmean(marker_hits), 1)
                    if marker_hits
                    else 0.0,
                },
            )
        )

    summary_data.sort(key=lambda item: (item[1]["avg_s"], -item[1]["avg_markers"]))

    table = Table(
        title="[bold cyan]Runner Summary[/bold cyan]",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Model", style="white", width=32)
    table.add_column("Runs", justify="right", width=6)
    table.add_column("OK", justify="right", width=4)
    table.add_column("Avg(s)", justify="right", width=8)
    table.add_column("P50(s)", justify="right", width=8)
    table.add_column("Max(s)", justify="right", width=8)
    table.add_column("Words", justify="right", width=8)
    table.add_column("Markers", justify="right", width=8)

    for name, metrics in summary_data:
        table.add_row(
            name,
            str(metrics["runs"]),
            str(metrics["ok"]),
            f"{metrics['avg_s']:.2f}",
            f"{metrics['p50_s']:.2f}",
            f"{metrics['max_s']:.2f}",
            f"{metrics['avg_words']:.0f}",
            f"{metrics['avg_markers']:.1f}",
        )

    return table


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live benchmark for Pollinations and Gemini models with LLM-as-a-Judge."
    )
    parser.add_argument(
        "--concurrency", type=int, default=3, help="Maximum concurrent benchmark calls."
    )
    parser.add_argument(
        "--save-json",
        default="",
        help="Optional path to save raw benchmark results as JSON.",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="Skip LLM-as-a-Judge evaluation.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    pollinations_key, gemini_key = load_keys(repo_root)

    console.print("\n[bold cyan]🔍 Kindly Model Benchmark[/bold cyan]")
    console.print(f"[dim]Endpoint:[/dim] {POLLI_ENDPOINT}")

    async with httpx.AsyncClient(timeout=90.0) as http_client:
        gemini_ids = await discover_gemini_models(gemini_key, http_client)
        if not gemini_ids:
            raise SystemExit(
                "Could not discover gemma model ids from the Gemini /models endpoint."
            )
        console.print("\n[bold]Gemini models discovered:[/bold]")
        for key in sorted(gemini_ids.keys()):
            console.print(f"  [cyan]{key}[/cyan]: {gemini_ids[key]}")

        prompts = PROMPTS
        runners = build_runners(gemini_ids)
        total_tasks = len(prompts) * len(runners)

        state = BenchmarkState(
            results=[None] * total_tasks,
            start_time=time.perf_counter(),
        )
        gemini_client = genai.Client(api_key=gemini_key)
        semaphore = asyncio.Semaphore(max(1, args.concurrency))
        persist_lock = asyncio.Lock()
        judgment_text = ""

        async def persist_outputs() -> None:
            async with persist_lock:
                save_markdown_report(
                    state.results,
                    prompts,
                    runners,
                    judgment_text,
                    DEFAULT_MARKDOWN_REPORT,
                )
                if args.save_json:
                    payload = build_json_payload(gemini_ids, state.results, judgment_text)
                    save_json_report(payload, args.save_json)

        console.print(
            f"\n[bold]Running benchmarks:[/bold] {len(prompts)} prompts × {len(runners)} models = {total_tasks} total"
        )

        tasks = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        ) as progress:
            
            main_task = progress.add_task("Running Prompts...", total=total_tasks)
            
            async def worker(index: int, runner: RunnerSpec, prompt: PromptSpec) -> None:
                async with semaphore:
                    started = time.perf_counter()
                    try:
                        result = await run_one(
                            runner,
                            prompt,
                            http_client,
                            pollinations_key=pollinations_key,
                            gemini_client=gemini_client,
                        )
                    except Exception as exc:
                        result = build_failed_result(
                            runner,
                            prompt,
                            exc,
                            time.perf_counter() - started,
                        )

                    state.results[index] = result
                    state.completed_count += 1
                    if not result.get("ok"):
                        state.error_count += 1
                    state.total_latency += result.get("elapsed_s", 0.0)
                    
                    status_color = "red" if not result.get("ok") else "green"
                    status_icon = "✗" if not result.get("ok") else "✔"
                    progress.console.print(
                        f"[{status_color}]{status_icon} {escape(runner['label'])} finished "
                        f"'[white]{escape(prompt['title'])}[/]' "
                        f"({result.get('elapsed_s', 0):.2f}s)[/]"
                    )
                    progress.advance(main_task)
                    progress.update(
                        main_task,
                        description=(
                            f"Running Prompts... completed {state.completed_count}/{total_tasks} | "
                            f"errors {state.error_count}"
                        ),
                    )
                    try:
                        await persist_outputs()
                    except Exception as exc:
                        progress.console.print(
                            f"[yellow]Autosave warning:[/] {escape(str(exc))}"
                        )

            async def dispatch_all():
                for i, (prompt, runner) in enumerate((p, r) for p in prompts for r in runners):
                    progress.console.print(
                        f"[dim]Queued {i + 1}/{total_tasks}: {escape(runner['label'])} "
                        f"<= {escape(prompt['title'])}[/dim]"
                    )
                    tasks.append(asyncio.create_task(worker(i, runner, prompt)))
                    if i < total_tasks - 1:
                        await asyncio.sleep(DISPATCH_INTERVAL_S)
            
            dispatch_task = asyncio.create_task(dispatch_all())
            
            while not dispatch_task.done() or any(not task.done() for task in tasks):
                await asyncio.sleep(0.25)
                
            await dispatch_task
            await asyncio.gather(*tasks)

        completed = [item for item in state.results if item is not None]
        console.print()
        console.print(render_summary_table(completed))

        if not args.no_judge:
            console.print(
                "\n[bold cyan]⚖️ Running LLM-as-a-Judge comparative evaluation...[/bold cyan]"
            )
            try:
                judgment_text = await run_judgments(
                    completed, prompts, gemini_client, console
                )
            except Exception as exc:
                judgment_text = f"❌ LLM Judge evaluation crashed: {type(exc).__name__}: {exc}"
                console.print(f"[bold red]{escape(judgment_text)}[/bold red]")

            await persist_outputs()
            console.print()
            console.print(
                Panel(
                    judgment_text,
                    title="[bold cyan]LLM Judge Comparative Analysis[/bold cyan]",
                    border_style="cyan",
                )
            )
        else:
            console.print(
                "[dim]Skipping LLM-as-a-Judge evaluation (--no-judge flag set)[/dim]"
            )
            await persist_outputs()

        if args.save_json:
            try:
                payload = build_json_payload(gemini_ids, state.results, judgment_text)
                save_json_report(payload, args.save_json)
                console.print(f"\n[green]Saved JSON report to {args.save_json}[/green]")
            except Exception as exc:
                console.print(
                    f"\n[bold yellow]Failed to save JSON report:[/] {escape(str(exc))}"
                )

        try:
            save_markdown_report(
                state.results,
                prompts,
                runners,
                judgment_text,
                DEFAULT_MARKDOWN_REPORT,
            )
            console.print(f"\n[green]Saved Markdown report to {DEFAULT_MARKDOWN_REPORT}[/green]")
        except Exception as exc:
            console.print(
                f"\n[bold yellow]Failed to save Markdown report:[/] {escape(str(exc))}"
            )

        success_count = sum(1 for item in completed if item.get("ok"))
        console.print(
            f"\n[bold]Benchmark complete:[/bold] {success_count}/{len(completed)} successful"
        )

    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
