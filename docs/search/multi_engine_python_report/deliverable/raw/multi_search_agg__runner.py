"""并发调度主逻辑 — ThreadPoolExecutor 全并发 + 引擎执行分发"""
import json, os, re, sys, time, urllib.parse, subprocess, platform as _platform
from collections.abc import Callable
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import signal as _signal

_IS_UNIX = _platform.system() != 'Windows'

# Windows 兼容：setsid / killpg
if _IS_UNIX:
    _SETSID = os.setsid
else:
    _SETSID = None  # Windows 不支持 preexec_fn=os.setsid

def _kill_proc_tree(proc, sig=_signal.SIGTERM):
    """跨平台进程清理"""
    if _IS_UNIX:
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, OSError):
            proc.terminate()
    else:
        proc.terminate()

from .config import WORKSPACE, TIMEOUT, MAX_WORKERS, RETRY_DELAY, OUTPUT_DIR
from .config import BAILIAN_KEY, ZHIPU_KEY, ZAI_KEY, AMINER_KEY, ZHIHU_KEY
from .models import SearchResult
from .engines import ENGINES, get_enabled_engines
from .engines.zhipu import call_zhipu_api
from .engines.zai import call_zai_api
from .engines.bailian import call_bailian_mcp
from .engines.serpbase import call_serpbase_api
from .engines.opencli import call_opencli
from .engines.web_fetch import call_web_fetch, parse_html_links
from .engines.mcp_stdio import call_mcp_stdio
from .engines.skill import call_skill
from . import ranker


def run_one(engine: dict, query: str) -> list[SearchResult]:
    """执行单个引擎查询，返回标准化结果"""
    name = engine["name"]
    etype = engine["type"]
    w = __import__("search_agg.config", fromlist=["WEIGHTS"]).WEIGHTS.get(name, 5)
    retry = engine.get("retry", False)

    def _exec():
        try:
            if etype == "zhipu_api":
                raw = call_zhipu_api(engine["engine"], query)
                return ranker.normalize_results(raw, name, w)
            elif etype == "serpbase_api":
                raw = call_serpbase_api(query)
                return ranker.normalize_results(raw, name, w)
            elif etype == "zai_api":
                raw = call_zai_api(query)
                return ranker.normalize_results(raw, name, w)
            elif etype == "bailian_mcp":
                raw = call_bailian_mcp(query)
                return ranker.normalize_results(raw, name, w)
            elif etype == "opencli" or etype == "opencli_heavy":
                raw = call_opencli(engine["site"], query, timeout=TIMEOUT)
                return ranker.normalize_results(raw, name, w)
            elif etype == "web_fetch":
                encoded_q = urllib.parse.quote(query)
                url = engine["url_tpl"].replace("{q}", encoded_q)
                html = call_web_fetch(url)
                if html:
                    return parse_html_links(html, name, w)
                return []
            elif etype == "skill":
                cmd = engine["cmd"].replace("{q}", query.replace('"', '\\"'))
                try:
                    # Process env var substitution
                    cmd = cmd.replace("$AMINER_API_KEY", AMINER_KEY or os.environ.get("AMINER_API_KEY", ""))
                    env = os.environ.copy()
                    import re as _re2
                    m = _re2.search(r'ZHIHU_ACCESS_SECRET=([^\s]+)', cmd)
                    if m:
                        env["ZHIHU_ACCESS_SECRET"] = m.group(1)
                        cmd = _re2.sub(r'ZHIHU_ACCESS_SECRET=\S+\s+', '', cmd)

                    proc = subprocess.Popen(
                        ["bash" if _IS_UNIX else "cmd", "-c" if _IS_UNIX else "/c", cmd],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=WORKSPACE,
                        env=env,
                        preexec_fn=_SETSID,
                    )
                    stdout, _ = proc.communicate(timeout=TIMEOUT)
                    t = stdout.decode("utf-8", errors="replace").strip()
                    if proc.returncode == 0 and t and t != "[]":
                        lines = t.split('\n')
                        json_str = '\n'.join(
                            l for l in lines
                            if l.strip() and not l.strip().startswith('[Cost]')
                        )
                        if not json_str.strip():
                            json_str = t
                        raw_resp = json.loads(json_str)
                        if isinstance(raw_resp, dict):
                            for key in ('data', 'items', 'results', 'hits'):
                                if key in raw_resp and isinstance(raw_resp[key], list):
                                    raw = raw_resp[key]
                                    break
                            else:
                                raw = []
                        else:
                            raw = raw_resp
                        return ranker.normalize_results(raw, name, w)
                except subprocess.TimeoutExpired:
                    _kill_proc_tree(proc, _signal.SIGTERM)
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        _kill_proc_tree(proc, _signal.SIGKILL if _IS_UNIX else _signal.SIGTERM)
                        proc.wait(timeout=1)
                except Exception:
                    try:
                        _kill_proc_tree(proc)
                    except Exception:
                        pass
                return []
            elif etype == "mcp_stdio":
                raw = call_mcp_stdio(
                    command=engine["command"],
                    args=engine.get("args", []),
                    tool=engine["tool"],
                    tool_args=engine["tool_args"],
                    query=query,
                    timeout=TIMEOUT,
                )
                return ranker.normalize_results(raw, name, w)
        except Exception:
            return []

    results = _exec()
    if not results and retry:
        time.sleep(RETRY_DELAY)
        results = _exec()
    return results


def aggregate(
    query: str,
    top_n: int = 50,
    engine_filter: list[str] | None = None,
    custom_timeout: int | None = None,
    progress_callback: Callable | None = None,
) -> dict:
    """主入口：并发调度所有引擎，去重排序，返回结果

    Args:
        query: 搜索查询词
        top_n: 返回前 N 条结果
        engine_filter: 限定引擎 id 列表（None=全部启用引擎）
        custom_timeout: 自定义超时（None=使用配置默认值）
        progress_callback: 可选回调，签名为 (engine_name: str, count: int) -> None
                           每个引擎完成后调用
    """
    global TIMEOUT
    effective_timeout = custom_timeout if custom_timeout is not None else TIMEOUT

    engines = get_enabled_engines(engine_filter)

    print(f"\n{'='*60}")
    print(f"🔍 {query}")
    print(f"   {len(engines)} 引擎 | 全并发 | {effective_timeout}s超时")
    print(f"{'='*60}")

    # 并行预热模型
    ranker.warmup_embedder()

    t0 = time.time()
    all_results = []
    stats = {}

    import threading
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_engine = {
            executor.submit(run_one, engine, query): engine
            for engine in engines
        }
        done_set = set()
        deadline = time.time() + effective_timeout
        try:
            remaining_time = max(1, deadline - time.time())
            for future in as_completed(future_to_engine, timeout=remaining_time):
                engine = future_to_engine[future]
                done_set.add(future)
                try:
                    results = future.result(timeout=0.1)
                except Exception:
                    results = []
                stats[engine["name"]] = len(results)
                all_results.extend(results)

                # 进度回调
                if progress_callback:
                    try:
                        progress_callback(engine["name"], len(results))
                    except Exception:
                        pass

                if time.time() > deadline:
                    break
        except (TimeoutError, KeyboardInterrupt):
            pass
        # 记录未完成的引擎
        for f, engine in future_to_engine.items():
            if f not in done_set and engine["name"] not in stats:
                stats[engine["name"]] = 0

    elapsed = time.time() - t0
    ok_count = sum(1 for c in stats.values() if c > 0)
    total_results = sum(stats.values())

    # 统计报告
    print(
        f"\n⏱️  {elapsed:.1f}s | ✅{ok_count} "
        f"❌{len(stats)-ok_count} 📦{total_results}\n"
    )
    print("引擎报告:")
    for name, count in sorted(stats.items(), key=lambda x: (-x[1], x[0])):
        icon = "✅" if count > 0 else "❌"
        print(f"  {icon} {name:20s} {count:>3d} 条")

    # 去重
    deduped = ranker.dedup(all_results)

    # 动态域名权威度
    domain_boost = ranker._domain_freq_boost(deduped)

    # 向量排序
    if ranker._EMBEDDER is not None:
        print("🧠 模型预热完成，开始向量排序")
    try:
        vector_scores = ranker.compute_vector_scores(query, deduped)
    except Exception as e:
        print(f"⚠️ 向量排序失败 ({e})，降级到纯关键词排序")
        vector_scores = {}

    # 排序
    now = datetime.now()
    for r in deduped:
        r._score = ranker.score_result(
            r, query, now,
            vector_scores=vector_scores,
            domain_boost=domain_boost,
        )
    deduped.sort(key=lambda r: r._score, reverse=True)
    ranked = ranker.diversify(deduped, top_n)

    # 附加元数据
    for r in ranked:
        r._vec_sim = round(vector_scores.get(r.url, 0), 4)
        try:
            d = r.url.split("/")[2].lower().replace("www.", "")
            r._domain_auth = domain_boost.get(d, 1.0)
        except Exception:
            r._domain_auth = 1.0

    # 输出 Top-N
    show_n = min(top_n, len(ranked))
    print(f"\n📊 {total_results}→{len(deduped)}去重→Top{show_n}\n")
    if ranked:
        for i, r in enumerate(ranked[:show_n], 1):
            line = f"{i}. [{r.source}] {r.title}"
            if r.publish_time:
                line += f" ({r.publish_time})"
            print(line)
            print(f"   {r.url}")
            if r.summary:
                s = r.summary[:200]
                if len(r.summary) > 200:
                    s += "..."
                print(f"   {s}")
            print()

    # 保存结果
    output = {
        "query": query,
        "timestamp": now.isoformat(),
        "elapsed_seconds": round(elapsed, 2),
        "engine_stats": stats,
        "total_engines": len(engines),
        "ok_engines": ok_count,
        "raw_results": total_results,
        "deduped": len(deduped),
        "top": len(ranked),
        "results": [
            {
                "rank": i + 1,
                "title": r.title,
                "url": r.url,
                "summary": r.summary,
                "source": r.source,
                "publish_time": r.publish_time,
                "author": r.author,
                "score": round(r._score, 4),
                "relevance": r._relevance,
                "vec_sim": getattr(r, '_vec_sim', 0),
                "domain_auth": getattr(r, '_domain_auth', 1.0),
                "engine_weight": r.engine_weight,
                "snippet_len": r.snippet_len,
            }
            for i, r in enumerate(ranked)
        ],
    }
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n💾 → {out_path}")

    return output
