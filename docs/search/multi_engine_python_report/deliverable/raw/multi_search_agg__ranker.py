"""结果排序、去重、打分模块"""
import math, os, re, time, sys, logging
from datetime import datetime
from typing import Optional
from .models import SearchResult
from .config import WORKSPACE

log = logging.getLogger("search_agg.ranker")


# ============================================================
# 通用结果解析
# ============================================================
def normalize_results(raw: list[dict], source: str, weight: int) -> list[SearchResult]:
    """将各种格式的原始结果统一为 SearchResult"""
    results = []
    if not isinstance(raw, list):
        return results
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", item.get("name", ""))).strip()
        url = str(
            item.get(
                "link", item.get("url", item.get("href", ""))
            )
        ).strip()
        summary = str(
            item.get(
                "content",
                item.get(
                    "snippet",
                    item.get(
                        "summary",
                        item.get(
                            "description",
                            item.get("abstract", ""),
                        ),
                    ),
                ),
            )
        ).strip()
        pub = item.get(
            "publish_date",
            item.get(
                "publish_time",
                item.get(
                    "date",
                    item.get(
                        "pub_year",
                        item.get(
                            "edit_time", item.get("published", None)
                        ),
                    ),
                ),
            ),
        )
        author = str(
            item.get(
                "author",
                item.get(
                    "first_author",
                    item.get(
                        "author_name",
                        item.get(
                            "inventor_name", item.get("authors", "")
                        ),
                    ),
                ),
            )
        ).strip()

        # opencli 引擎无摘要时，从元数据构造合成摘要
        if not summary:
            parts = []
            if author:
                parts.append(f"by {author}")
            tags = item.get("tags", "")
            if tags:
                parts.append(f"tags: {tags}")
            cat = item.get("primary_category", "")
            if cat:
                parts.append(f"category: {cat}")
            venue = item.get("venue", "")
            typ = item.get("type", "")
            if venue:
                parts.append(f"venue: {venue}")
            if typ:
                parts.append(f"type: {typ}")
            score = item.get("score", "")
            comments = item.get("comments", "")
            if score:
                parts.append(f"score: {score}")
            if comments:
                parts.append(f"comments: {comments}")
            ver = item.get("version", "")
            dl = item.get("weeklyDownloads", "")
            if ver:
                parts.append(f"v{ver}")
            if dl:
                parts.append(f"{dl}/week")
            publisher = item.get("publisher", "")
            if publisher:
                parts.append(f"by {publisher}")
            doi = item.get("doi", "")
            if doi:
                parts.append(f"doi: {doi}")
            if parts:
                summary = " | ".join(parts)

        # AMiner 专利/论文无 URL 时构造链接
        if not url and item.get("id"):
            aid = item["id"]
            if "专利" in source:
                url = f"https://www.aminer.cn/patent/{aid}"
            else:
                url = f"https://www.aminer.cn/pub/{aid}"

        if not title or not url:
            continue

        pub_str = None
        if pub:
            pub_str = str(pub)

        results.append(
            SearchResult(
                title=title[:200],
                url=url,
                summary=summary[:500] if summary else "",
                source=source,
                publish_time=pub_str,
                author=author,
                snippet_len=len(summary) if summary else 0,
                engine_weight=weight,
            )
        )
    return results


# ============================================================
# 去重
# ============================================================
def dedup(results: list[SearchResult]) -> list[SearchResult]:
    """URL 精确去重 + 标题相似度去重 + 跨引擎交叉验证"""
    # 第一轮：URL 精确去重，跨引擎交叉验证加分
    url_map = {}  # normalized_url → SearchResult
    url_sources = {}  # normalized_url → set of source names
    for r in results:
        n = r.normalize_url()
        if not n:
            continue
        if n not in url_sources:
            url_sources[n] = set()
        url_sources[n].add(r.source)
        # 保留摘要最长的版本
        if n not in url_map or r.snippet_len > url_map[n].snippet_len:
            url_map[n] = r

    # 记录跨引擎确认数
    for url, r in url_map.items():
        cross_count = len(url_sources.get(url, {r.source}))
        r._cross_engine_count = max(cross_count, 1)

    # 第二轮：标题前 60 字符相似度去重
    title_map = {}
    for r in url_map.values():
        key = re.sub(r"[^\w\s]", "", r.title[:60].lower().strip())
        if not key or len(key) < 5:
            continue
        if key not in title_map:
            title_map[key] = r
        else:
            existing = title_map[key]
            if (
                r._cross_engine_count > existing._cross_engine_count
                or (
                    r._cross_engine_count == existing._cross_engine_count
                    and r.snippet_len > existing.snippet_len
                )
            ):
                title_map[key] = r

    return list(title_map.values())


# ============================================================
# 向量排序 — Jina v5 Embedding (本地 transformers 直加载)
# ============================================================
_EMBEDDER = None
_EMBEDDER_LOADING = False


def _gpu_free_mb() -> float:
    """返回 GPU 空闲显存（MiB），失败返回 0。"""
    try:
        import subprocess
        out = subprocess.check_output(
            ["nvidia-smi",
             "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            timeout=5, text=True)
        return float(out.strip().splitlines()[0].strip())
    except Exception:
        return 0


class _LocalEmbedder:
    """直接用 transformers 加载 Jina v5，不依赖 vLLM 服务。

    GPU 显存充足时用 CUDA，否则自动降级 CPU。完全独立，零外部服务依赖。
    """
    def __init__(self, model_path: str, device: str = "cpu", truncate_dim: int = 512):
        import torch
        from transformers import AutoTokenizer, AutoModel
        self.device = torch.device(device if torch.cuda.is_available() and device == "cuda" else "cpu")
        self.truncate_dim = truncate_dim
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        self.model = AutoModel.from_pretrained(model_path, trust_remote_code=True).to(self.device).eval()
        log.info(f"JinaV5 loaded on {self.device}")

    def encode(self, texts: list[str], **kwargs) -> "numpy.ndarray":
        import torch, numpy as np
        batch_size = 32
        all_vecs = []
        with torch.no_grad():
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                inputs = self.tokenizer(batch, return_tensors="pt", truncation=True,
                                       max_length=512, padding=True).to(self.device)
                out = self.model(**inputs)
                vecs = out.last_hidden_state[:, 0, :].cpu().numpy()
                norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-8
                vecs = vecs / norms
                if self.truncate_dim and self.truncate_dim < vecs.shape[1]:
                    vecs = vecs[:, :self.truncate_dim]
                    vecs = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-8)
                all_vecs.append(vecs)
        return np.vstack(all_vecs)


def _choose_device() -> str:
    """根据 GPU 空闲显存选择设备：>=1GiB 用 cuda，否则降级 cpu。"""
    free = _gpu_free_mb()
    if free >= 1024:
        return "cuda"
    log.info(f"GPU free memory only {free:.0f} MiB, falling back to CPU for vector ranking")
    return "cpu"


def _model_path() -> str:
    return os.environ.get(
        "JINA_MODEL_PATH",
        os.path.expanduser("~/models/jina-embeddings-v5-text-small/")
    )


def warmup_embedder():
    """后台预热模型，与搜索并行。调用方不阻塞。"""
    global _EMBEDDER, _EMBEDDER_LOADING
    if _EMBEDDER is not None or _EMBEDDER_LOADING:
        return
    model_path = _model_path()
    if not os.path.isdir(model_path):
        log.warning(
            f"Jina v5 model not found at: {model_path}. "
            f"Vector ranking disabled. Download with: "
            f"huggingface-cli download jinaai/jina-embeddings-v5-text-small "
            f"--local-dir {model_path}"
        )
        return
    _EMBEDDER_LOADING = True
    import threading

    def _load():
        global _EMBEDDER, _EMBEDDER_LOADING
        try:
            device = _choose_device()
            _EMBEDDER = _LocalEmbedder(
                model_path=model_path, device=device, truncate_dim=512)
        except Exception as e:
            log.warning(f"Failed to load Jina v5 embedder: {e}")
        finally:
            _EMBEDDER_LOADING = False

    threading.Thread(target=_load, daemon=True).start()


def _get_embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        model_path = _model_path()
        if not os.path.isdir(model_path):
            log.warning(f"Jina v5 model not found at: {model_path}. Vector ranking disabled.")
            return None
        try:
            device = _choose_device()
            _EMBEDDER = _LocalEmbedder(
                model_path=model_path, device=device, truncate_dim=512)
        except Exception as e:
            log.warning(f"Failed to load embedder: {e}")
            return None
    return _EMBEDDER


def compute_vector_scores(query: str, results: list[SearchResult]) -> dict:
    """用 Jina v5 计算query与所有result的余弦相似度，返回{url: sim_score}"""
    if not results:
        return {}
    t0 = time.time()
    embedder = _get_embedder()

    q_vec = embedder.encode([query])  # (1, 512)

    docs = []
    urls = []
    for r in results:
        text = r.title
        if r.summary:
            text += "\n" + r.summary[:500]
        docs.append(text)
        urls.append(r.url)

    d_vecs = embedder.encode(docs)  # (N, 512)

    # 健康检查：验证向量不全相同
    if d_vecs.shape[0] >= 2:
        diff = float(((d_vecs[0] - d_vecs[1]) ** 2).sum())
        if diff < 1e-6:
            log.warning("All embedding vectors identical — vector ranking degraded")
            return {}

    sims = (d_vecs @ q_vec.T).flatten()

    elapsed = time.time() - t0
    print(f"\n🧠 向量排序: {len(docs)} 条 × 512维, {elapsed:.2f}s")

    return {url: float(sim) for url, sim in zip(urls, sims)}


# ============================================================
# 排序算法 — 向量+多维混合
# ============================================================
def compute_relevance(result: SearchResult, query: str) -> float:
    """计算查询相关度（0-1）"""
    q_terms = set(re.findall(r'\w+', query.lower()))
    if not q_terms:
        return 0.5

    title_lower = result.title.lower()
    summary_lower = result.summary.lower() if result.summary else ""

    title_hits = sum(1 for t in q_terms if t in title_lower)
    title_coverage = title_hits / len(q_terms)

    title_chars = sum(len(t) for t in q_terms if t in title_lower)
    query_chars = sum(len(t) for t in q_terms)
    title_containment = title_chars / query_chars if query_chars else 0

    summary_hits = sum(1 for t in q_terms if t in summary_lower)
    summary_coverage = summary_hits / len(q_terms)

    relevance = (
        0.5 * title_coverage
        + 0.3 * title_containment
        + 0.2 * summary_coverage
    )
    return min(1.0, relevance)


def compute_recency(publish_time: Optional[str], now: datetime) -> float:
    """计算时效分（0-1）"""
    if not publish_time:
        return 0.3

    try:
        pt = str(publish_time)
        for fmt in [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y/%m/%d",
            "%Y年%m月%d日",
        ]:
            try:
                pub_dt = datetime.strptime(
                    pt.split('(')[0].split('（')[0].strip(), fmt
                )
                days = max(0, (now - pub_dt).days)
                return 2 ** (-days / 60)
            except ValueError:
                continue
        pub_dt = datetime.fromisoformat(
            pt.replace("Z", "+00:00").replace("+08:00", "")
        )
        days = max(0, (now - pub_dt.replace(tzinfo=None)).days)
        return 2 ** (-days / 60)
    except Exception:
        return 0.3


def compute_snippet_quality(result: SearchResult) -> float:
    """摘要质量分（0-1）"""
    if result.snippet_len > 0:
        return min(1.0, result.snippet_len / 200)
    return 0.1


def _domain_freq_boost(
    all_raw_results: list[SearchResult], top_n: int = 200
) -> dict:
    """动态域名权威度：基于域名在所有原始结果中的出现频次"""
    domain_counts = {}
    for r in all_raw_results[:top_n]:
        try:
            d = r.url.split("/")[2].lower().replace("www.", "")
        except (IndexError, AttributeError):
            continue
        domain_counts[d] = domain_counts.get(d, 0) + 1

    if not domain_counts:
        return {}

    max_log = math.log2(max(1, max(domain_counts.values())))
    boost_map = {}
    for domain, count in domain_counts.items():
        if count <= 1:
            boost_map[domain] = 0.8
        else:
            log_val = math.log2(count)
            boost_map[domain] = round(0.9 + 0.6 * (log_val / max_log), 2)
    return boost_map


def score_result(
    result: SearchResult,
    query: str,
    now: datetime,
    vector_scores: dict | None = None,
    domain_boost: dict | None = None,
) -> float:
    """综合评分：向量(0.5) + 时效(0.3) + 引擎权重(0.2) × 域名权威度 × 交叉确认"""
    relevance = compute_relevance(result, query)
    recency = compute_recency(result.publish_time, now)
    snippet_q = compute_snippet_quality(result)

    result._relevance = round(relevance, 3)

    if vector_scores:
        vec_sim = vector_scores.get(result.url, 0.5)
        base = (
            0.5 * vec_sim
            + 0.3 * recency
            + 0.2 * min(1.0, result.engine_weight / 10)
        )
    else:
        base = (
            0.5 * relevance
            + 0.15 * snippet_q
            + 0.2 * recency
            + 0.15 * min(1.0, result.engine_weight / 10)
        )

    cross = getattr(result, '_cross_engine_count', 1)
    cross_boost = 1.0 + 0.15 * max(0, cross - 1)

    try:
        domain = result.url.split("/")[2].lower().replace("www.", "")
    except (IndexError, AttributeError):
        domain = ""
    domain_auth = domain_boost.get(domain, 1.0) if domain_boost else 1.0

    score = base * domain_auth * cross_boost * 100
    return score


def diversify(
    results: list[SearchResult], top_n: int
) -> list[SearchResult]:
    """引擎多样性分散：同引擎结果不连续超过 3 条，同域名不连续超过 2 条"""
    ranked = sorted(results, key=lambda r: r._score, reverse=True)
    output = []
    remaining = list(ranked)
    source_run = {}
    domain_run = {}

    while remaining and len(output) < top_n:
        placed = False
        for i, r in enumerate(remaining):
            src = r.source
            domain = r.url.split('/')[2] if '/' in r.url[8:] else r.url

            src_ok = source_run.get(src, 0) < 3
            dom_ok = domain_run.get(domain, 0) < 2

            if src_ok and dom_ok:
                output.append(r)
                remaining.pop(i)
                for s in list(source_run.keys()):
                    source_run[s] = 0 if s != src else source_run.get(s, 0) + 1
                for d in list(domain_run.keys()):
                    domain_run[d] = (
                        0 if d != domain else domain_run.get(d, 0) + 1
                    )
                placed = True
                break

        if not placed:
            for i, r in enumerate(remaining):
                src = r.source
                if source_run.get(src, 0) < 5:
                    output.append(r)
                    remaining.pop(i)
                    for s in list(source_run.keys()):
                        source_run[s] = (
                            0 if s != src else source_run.get(s, 0) + 1
                        )
                    placed = True
                    break

        if not placed and remaining:
            output.append(remaining.pop(0))
            source_run = {}
            domain_run = {}

    return output
