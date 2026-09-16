"""
Multi-Search Aggregator for AI Agents
======================================
聚合 30+ 搜索引擎的并发搜索聚合器，专为 AI Agent 场景定制。

Usage:
    from search_agg import aggregate, search, SearchResult

    results = aggregate("RISC-V vector extension", top_n=20)
    for r in results["results"]:
        print(r["title"], r["url"])

    # 限定引擎
    results = aggregate("LDPC decoder", engine_filter=["zhipu_pro", "arxiv", "wf_bing_int"])

    # 简便 API（用于 Web 服务）
    results = search("RISC-V vector extension", top_k=50, engines=["zhipu_pro", "arxiv"])
"""

from collections.abc import Callable

from .models import SearchResult
from .runner import aggregate
from .engines import ENGINES, get_enabled_engines

__all__ = ["aggregate", "search", "SearchResult", "ENGINES", "get_enabled_engines"]
__version__ = "8.1.0"


def search(
    query: str,
    top_k: int = 50,
    engines: list[str] | None = None,
    timeout: int = 35,
    progress_callback: Callable | None = None,
) -> dict:
    """简便搜索函数，供 API 调用

    Args:
        query: 搜索查询词
        top_k: 返回前 K 条结果
        engines: 限定引擎 id 列表（None=全部启用引擎）
        timeout: 超时秒数
        progress_callback: 可选回调 (engine_name, result_count) -> None

    Returns:
        dict with keys: results (list), meta (dict with query, engines_used,
        total_results, returned, elapsed_ms, timed_out)
    """
    import time
    t0 = time.time()

    raw = aggregate(
        query=query,
        top_n=top_k,
        engine_filter=engines,
        custom_timeout=timeout,
        progress_callback=progress_callback,
    )

    elapsed_ms = int((time.time() - t0) * 1000)

    # 转为标准 API 响应格式
    api_results = []
    for r in raw.get("results", []):
        api_results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "summary": r.get("summary", ""),
            "source": r.get("source", ""),
            "publish_time": r.get("publish_time"),
            "author": r.get("author", ""),
            "score": r.get("score", 0),
            "relevance": r.get("relevance", 0),
            "vec_sim": r.get("vec_sim", 0),
            "domain_auth": r.get("domain_auth", 1.0),
            "engine_weight": r.get("engine_weight", 5),
        })

    ok_engines = raw.get("ok_engines", 0)
    total_engines = raw.get("total_engines", 0)

    return {
        "results": api_results,
        "meta": {
            "query": query,
            "engines_used": ok_engines,
            "total_engines": total_engines,
            "total_results": raw.get("raw_results", 0),
            "returned": len(api_results),
            "elapsed_ms": elapsed_ms,
            "timed_out": raw.get("ok_engines", 0) < raw.get("total_engines", 0),
            "engine_stats": raw.get("engine_stats", {}),
        },
    }
