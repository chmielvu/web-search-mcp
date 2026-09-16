"""
FastAPI server for Multi-Search Aggregator
- REST API for aggregated search
- Config management API
- Static file serving for web UI
"""
import asyncio
import json
import logging
import os
import sys
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    StreamingResponse,
    JSONResponse,
    HTMLResponse,
    FileResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 项目路径设置：确保 search_agg 导入可用
# ---------------------------------------------------------------------------
_PROJECT_DIR = Path(__file__).resolve().parent
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("search-server")


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------
class SearchRequest(BaseModel):
    query: str = Field(..., description="搜索查询词")
    top_k: int = Field(default=50, ge=1, le=200, description="返回结果数")
    engines: Optional[list[str]] = Field(default=None, description="限定引擎 id 列表，空=全部")
    timeout: int = Field(default=35, ge=5, le=120, description="超时秒数")


class EngineToggleRequest(BaseModel):
    engine_id: str = Field(..., description="引擎 ID")
    enabled: bool = Field(..., description="启用/禁用")


class ConfigUpdateRequest(BaseModel):
    """部分配置更新 — 只更新提供的字段"""
    timeout: Optional[int] = None
    max_workers: Optional[int] = None
    default_top_k: Optional[int] = None
    max_top_k: Optional[int] = None
    default_timeout: Optional[int] = None
    engine_overrides: Optional[dict] = None  # {engine_id: {enabled: bool, ...}}

    class Config:
        extra = "ignore"


class ErrorResponse(BaseModel):
    error: str
    detail: str


# ---------------------------------------------------------------------------
# 应用生命周期
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("🚀 Multi-Search Aggregator server 启动中...")
    # 验证配置加载
    try:
        from search_agg.config import SERVER_PORT, SERVER_HOST
        from search_agg.engines import ENGINES

        enabled = sum(
            1 for e in ENGINES
            if _check_engine_enabled(e["id"])
        )
        logger.info(f"   配置已加载: {len(ENGINES)} 引擎（{enabled} 启用）")
        logger.info(f"   监听: {SERVER_HOST}:{SERVER_PORT}")
    except Exception:
        logger.warning("   配置加载警告")

    yield  # 应用运行

    logger.info("🛑 服务器关闭")


app = FastAPI(
    title="Multi-Search Aggregator API",
    description="聚合 30+ 搜索引擎 — REST API 与 Web 服务",
    version="8.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS 配置
# ---------------------------------------------------------------------------
try:
    from search_agg.config import SERVER_CORS_ORIGINS
except Exception:
    SERVER_CORS_ORIGINS = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=SERVER_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# 引擎状态辅助函数
# ---------------------------------------------------------------------------
def _check_engine_enabled(engine_id: str) -> bool:
    """检查引擎是否启用"""
    try:
        from search_agg.config import is_engine_enabled
        return is_engine_enabled(engine_id)
    except Exception:
        return True


def _has_api_key(engine: dict) -> bool:
    """检查引擎是否有对应的 API key"""
    etype = engine["type"]
    engine_id = engine["id"]

    if etype in ("zhipu_api",):
        key = os.environ.get("ZHIPU_API_KEY", "")
        return bool(key)
    elif etype == "zai_api":
        key = os.environ.get("ZAI_API_KEY", "")
        return bool(key)
    elif etype == "bailian_mcp":
        key = os.environ.get("BAILIAN_API_KEY", "")
        return bool(key)
    elif etype == "skill":
        if "aminer" in engine_id.lower():
            key = os.environ.get("AMINER_API_KEY", "")
            return bool(key)
        if "zhihu" in engine_id.lower():
            key = os.environ.get("ZHIHU_ACCESS_SECRET", "")
            return bool(key)
        return True  # 其他 skill 不需要特殊 key
    elif etype in ("opencli", "opencli_heavy"):
        return True  # opencli 通过本地 CLI 调用，不检查 key
    elif etype == "web_fetch":
        return True  # web_fetch 无需 key
    elif etype == "mcp_stdio":
        return True  # MCP stdio 直连
    return True


# ---------------------------------------------------------------------------
# 全局线程池 — 用于 CPU 密集型搜索任务
# ---------------------------------------------------------------------------
try:
    from search_agg.config import SEARCH_MAX_WORKERS, DEFAULT_TOP_K, MAX_TOP_K, DEFAULT_TIMEOUT
except Exception:
    SEARCH_MAX_WORKERS = 40
    DEFAULT_TOP_K = 50
    MAX_TOP_K = 200
    DEFAULT_TIMEOUT = 35

# ---------------------------------------------------------------------------
# 辅助函数：执行搜索（在线程池中）
# ---------------------------------------------------------------------------
async def _run_search(
    query: str,
    top_k: int = 50,
    engines: list[str] | None = None,
    timeout: int = 35,
    progress_queue: asyncio.Queue | None = None,
) -> dict:
    """在线程中执行聚合搜索，可选通过 progress_queue 推送进度"""

    def _progress_callback(engine_name: str, count: int) -> None:
        """线程安全的进度回调"""
        if progress_queue is not None:
            try:
                # 使用 call_soon_threadsafe 将事件放入事件循环
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(
                    progress_queue.put_nowait,
                    {"engine": engine_name, "results": count},
                )
            except Exception:
                pass

    def _do_search():
        from search_agg import search as do_search

        return do_search(
            query=query,
            top_k=top_k,
            engines=engines,
            timeout=timeout,
            progress_callback=_progress_callback,
        )

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do_search)


# ---------------------------------------------------------------------------
# ==================== API 端点 ====================
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------
# 搜索 — POST
# ---------------------------------------------------------------
@app.post("/api/search")
async def api_search_post(req: SearchRequest):
    """聚合搜索 — POST 请求"""
    try:
        top_k = min(req.top_k, MAX_TOP_K)
        result = await _run_search(
            query=req.query,
            top_k=top_k,
            engines=req.engines,
            timeout=req.timeout,
        )
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"搜索异常: {e}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail={"error": "搜索执行失败", "detail": str(e)},
        )


# ---------------------------------------------------------------
# 搜索 — GET
# ---------------------------------------------------------------
@app.get("/api/search")
async def api_search_get(
    q: str = Query(..., description="搜索查询词"),
    top_k: int = Query(default=50, ge=1, le=200, description="返回结果数"),
    engines: Optional[str] = Query(default=None, description="逗号分隔的引擎 id"),
    timeout: int = Query(default=35, ge=5, le=120, description="超时秒数"),
):
    """聚合搜索 — GET 请求"""
    try:
        engine_list = None
        if engines:
            engine_list = [e.strip() for e in engines.split(",") if e.strip()]

        top_k = min(top_k, MAX_TOP_K)
        result = await _run_search(
            query=q,
            top_k=top_k,
            engines=engine_list,
            timeout=timeout,
        )
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"搜索异常: {e}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail={"error": "搜索执行失败", "detail": str(e)},
        )


# ---------------------------------------------------------------
# 搜索 — SSE 流式进度
# ---------------------------------------------------------------
@app.get("/api/search/stream")
async def api_search_stream(
    q: str = Query(..., description="搜索查询词"),
    top_k: int = Query(default=50, ge=1, le=200),
    engines: Optional[str] = Query(default=None),
    timeout: int = Query(default=35, ge=5, le=120),
):
    """聚合搜索 — Server-Sent Events 实时进度推送"""

    async def event_generator():
        progress_queue: asyncio.Queue = asyncio.Queue()
        engine_list = None
        if engines:
            engine_list = [e.strip() for e in engines.split(",") if e.strip()]

        # 在后台启动搜索任务
        search_task = asyncio.ensure_future(
            _run_search(
                query=q,
                top_k=min(top_k, MAX_TOP_K),
                engines=engine_list,
                timeout=timeout,
                progress_queue=progress_queue,
            )
        )

        # 消息计数器
        msg_id = 0

        # 持续推送进度事件直到搜索完成
        result_received = False
        last_progress_time = time.time()

        while not result_received:
            try:
                # 等待进度事件或超时
                event = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                msg_id += 1
                yield f"id: {msg_id}\nevent: engine_done\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                last_progress_time = time.time()
            except asyncio.TimeoutError:
                # 检查搜索是否已完成
                if search_task.done():
                    result_received = True
                    break
                # 发送心跳避免连接断开
                if time.time() - last_progress_time > 10:
                    msg_id += 1
                    yield f"id: {msg_id}\nevent: heartbeat\ndata: {{\"alive\": true}}\n\n"
                    last_progress_time = time.time()

        # 搜索完成，推送最终结果
        try:
            result = await search_task
        except Exception as e:
            msg_id += 1
            yield (
                f"id: {msg_id}\nevent: error\n"
                f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
            )
            return

        msg_id += 1
        yield f"id: {msg_id}\nevent: complete\ndata: {json.dumps(result['meta'], ensure_ascii=False)}\n\n"

        # 附带发射每个结果（可选：客户端可逐步渲染）
        for i, r in enumerate(result.get("results", [])):
            msg_id += 1
            yield f"id: {msg_id}\nevent: result\ndata: {json.dumps(r, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------
# 引擎管理 — 列出所有引擎
# ---------------------------------------------------------------
@app.get("/api/engines")
async def api_engines_list():
    """列出所有搜索引擎及其状态"""
    try:
        from search_agg.engines import ENGINES

        engine_list = []
        for e in ENGINES:
            enabled = _check_engine_enabled(e["id"])
            has_key = _has_api_key(e)
            engine_list.append({
                "id": e["id"],
                "name": e["name"],
                "type": e["type"],
                "enabled": enabled,
                "has_key": has_key,
                "status": (
                    "ready" if (enabled and has_key)
                    else "disabled" if not enabled
                    else "no_key"
                ),
                "retry": e.get("retry", False),
            })

        stats = {
            "total": len(engine_list),
            "enabled": sum(1 for e in engine_list if e["enabled"]),
            "ready": sum(1 for e in engine_list if e["status"] == "ready"),
            "disabled": sum(1 for e in engine_list if e["status"] == "disabled"),
            "no_key": sum(1 for e in engine_list if e["status"] == "no_key"),
        }

        return JSONResponse(content={
            "engines": engine_list,
            "stats": stats,
        })
    except Exception as e:
        logger.error(f"引擎列表异常: {e}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail={"error": "获取引擎列表失败", "detail": str(e)},
        )


# ---------------------------------------------------------------
# 引擎管理 — 启用/禁用
# ---------------------------------------------------------------
@app.post("/api/engines/toggle")
async def api_engines_toggle(req: EngineToggleRequest):
    """启用或禁用一个搜索引擎（运行时）"""
    try:
        from search_agg.engines import get_engine_by_id

        engine = get_engine_by_id(req.engine_id)
        if engine is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "引擎不存在", "detail": f"未知引擎: {req.engine_id}"},
            )

        # 更新运行时配置
        import search_agg.config as cfg_mod

        overrides = cfg_mod._CONFIG.setdefault("engine_overrides", {})
        entry = overrides.setdefault(req.engine_id, {})
        entry["enabled"] = req.enabled

        # 清除模块级缓存，强制重新加载
        if hasattr(cfg_mod, "_engine_enabled_cache"):
            del cfg_mod._engine_enabled_cache

        status = "启用" if req.enabled else "禁用"
        logger.info(f"引擎 '{engine['name']}' ({req.engine_id}) → {status}")

        return JSONResponse(content={
            "engine_id": req.engine_id,
            "engine_name": engine["name"],
            "enabled": req.enabled,
            "message": f"引擎 '{engine['name']}' 已{status}",
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"引擎切换异常: {e}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail={"error": "引擎切换失败", "detail": str(e)},
        )


# ---------------------------------------------------------------
# 配置管理 — 获取配置
# ---------------------------------------------------------------
@app.get("/api/config")
async def api_config_get():
    """返回当前配置（API keys 已脱敏）"""
    try:
        import search_agg.config as cfg_mod

        # 收集完整配置
        config = {
            "server": {
                "host": cfg_mod.SERVER_HOST,
                "port": cfg_mod.SERVER_PORT,
                "cors_origins": cfg_mod.SERVER_CORS_ORIGINS,
            },
            "search": {
                "default_top_k": cfg_mod.DEFAULT_TOP_K,
                "max_top_k": cfg_mod.MAX_TOP_K,
                "default_timeout": cfg_mod.DEFAULT_TIMEOUT,
                "max_workers": cfg_mod.SEARCH_MAX_WORKERS,
            },
            "timeout": cfg_mod.TIMEOUT,
            "max_workers": cfg_mod.MAX_WORKERS,
            "retry_delay": cfg_mod.RETRY_DELAY,
            "workspace": cfg_mod.WORKSPACE,
            "output_dir": cfg_mod.OUTPUT_DIR,
            "weights": cfg_mod.WEIGHTS,
            "engine_overrides": cfg_mod._CONFIG.get("engine_overrides", {}),
            "api_keys": {
                "bailian": "已配置" if os.environ.get("BAILIAN_API_KEY") else "未配置",
                "zhipu": "已配置" if os.environ.get("ZHIPU_API_KEY") else "未配置",
                "zai": "已配置" if os.environ.get("ZAI_API_KEY") else "未配置",
                "aminer": "已配置" if os.environ.get("AMINER_API_KEY") else "未配置",
                "zhihu": "已配置" if os.environ.get("ZHIHU_ACCESS_SECRET") else "未配置",
                "opencli": os.environ.get("OPENCLI_PATH", "opencli"),
            },
        }

        return JSONResponse(content=config)
    except Exception as e:
        logger.error(f"获取配置异常: {e}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail={"error": "获取配置失败", "detail": str(e)},
        )


# ---------------------------------------------------------------
# 配置管理 — 更新配置（运行时）
# ---------------------------------------------------------------
@app.put("/api/config")
async def api_config_update(request: Request):
    """运行时更新部分配置 — 接受任意 JSON body"""
    try:
        import search_agg.config as cfg_mod
        body = await request.json()
        changes = []

        # 顶层字段
        if "timeout" in body:
            cfg_mod.TIMEOUT = int(body["timeout"])
            changes.append(f"timeout={body['timeout']}")
        if "max_workers" in body:
            cfg_mod.MAX_WORKERS = int(body["max_workers"])
            changes.append(f"max_workers={body['max_workers']}")
        if "default_top_k" in body:
            cfg_mod.DEFAULT_TOP_K = int(body["default_top_k"])
            changes.append(f"default_top_k={body['default_top_k']}")
        if "default_timeout" in body:
            cfg_mod.DEFAULT_TIMEOUT = int(body["default_timeout"])
            changes.append(f"default_timeout={body['default_timeout']}")

        # engine_overrides
        if "engine_overrides" in body:
            overrides = cfg_mod._CONFIG.setdefault("engine_overrides", {})
            overrides.update(body["engine_overrides"])
            changes.append(f"engine_overrides updated ({len(body['engine_overrides'])} engines)")

        # server config (write to _CONFIG so it persists for this session)
        if "server" in body:
            sc = cfg_mod._CONFIG.setdefault("server", {})
            sc.update(body["server"])
            cfg_mod.SERVER_HOST = sc.get("host", cfg_mod.SERVER_HOST)
            cfg_mod.SERVER_PORT = int(sc.get("port", cfg_mod.SERVER_PORT))
            changes.append(f"server config updated")

        # search config
        if "search" in body:
            s = cfg_mod._CONFIG.setdefault("search", {})
            s.update(body["search"])
            if "default_top_k" in body["search"]:
                cfg_mod.DEFAULT_TOP_K = int(body["search"]["default_top_k"])
                changes.append(f"search.default_top_k={body['search']['default_top_k']}")
            if "max_workers" in body["search"]:
                cfg_mod.MAX_WORKERS = int(body["search"]["max_workers"])
                changes.append(f"search.max_workers={body['search']['max_workers']}")

        logger.info(f"Config updated: {', '.join(changes)}")
        return JSONResponse(content={"message": "Config updated", "changes": changes})
    except Exception as e:
        logger.error(f"Config update error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail={"error": "Config update failed", "detail": str(e)})


# ---------------------------------------------------------------
# 配置管理 — 重新加载
# ---------------------------------------------------------------
@app.post("/api/config/reload")
async def api_config_reload():
    """重新加载 config.yaml"""
    try:
        import importlib
        import search_agg.config as cfg_mod

        # 强制重新加载配置模块
        importlib.reload(cfg_mod)

        # 验证加载结果
        engine_count = 0
        try:
            from search_agg.engines import ENGINES
            engine_count = len(ENGINES)
        except Exception:
            pass

        logger.info("配置已重新加载")

        return JSONResponse(content={
            "message": "配置已重新加载",
            "engines_loaded": engine_count,
            "server_port": getattr(cfg_mod, "SERVER_PORT", 8200),
        })
    except Exception as e:
        logger.error(f"重新加载配置异常: {e}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail={"error": "配置重新加载失败", "detail": str(e)},
        )


# ---------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------
@app.get("/api/health")
async def api_health():
    """健康检查 — 各引擎连通性"""
    try:
        from search_agg.engines import ENGINES

        checks = []
        now = datetime.now().isoformat()

        for e in ENGINES:
            enabled = _check_engine_enabled(e["id"])
            has_key = _has_api_key(e)
            checks.append({
                "id": e["id"],
                "name": e["name"],
                "type": e["type"],
                "enabled": enabled,
                "has_key": has_key,
                "status": "healthy" if (enabled and has_key) else ("disabled" if not enabled else "no_api_key"),
            })

        healthy = sum(1 for c in checks if c["status"] == "healthy")
        total = len(checks)

        return JSONResponse(content={
            "status": "ok",
            "timestamp": now,
            "engine_count": total,
            "healthy_engines": healthy,
            "engines": checks,
        })
    except Exception as e:
        logger.error(f"健康检查异常: {e}\n{traceback.format_exc()}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error": "健康检查失败",
                "detail": str(e),
            },
        )


# ---------------------------------------------------------------
# 根路径 — 返回静态首页
# ---------------------------------------------------------------
@app.get("/")
async def root():
    """根路径 — 返回 Web UI"""
    index_path = _PROJECT_DIR / "static" / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Multi-Search Aggregator API</h1><p>访问 /docs 查看 API 文档</p>")


# ---------------------------------------------------------------
# 全局异常处理
# ---------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理 — 确保返回统一错误格式"""
    logger.error(f"未捕获异常: {exc}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "服务器内部错误",
            "detail": str(exc),
        },
    )


# ---------------------------------------------------------------------------
# 静态文件挂载（放在最后，避免覆盖 API 路由）
# ---------------------------------------------------------------------------
static_dir = _PROJECT_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ---------------------------------------------------------------------------
# 主入口：python server.py 直接启动
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    try:
        host = SERVER_CORS_ORIGINS  # 这里 host 是局域网
    except Exception:
        host = "0.0.0.0"

    # 读取最终端口
    try:
        from search_agg.config import SERVER_HOST, SERVER_PORT
        host = SERVER_HOST
        port = SERVER_PORT
    except Exception:
        port = int(os.environ.get("SEARCH_PORT", 8200))

    logger.info(f"启动服务器: {host}:{port}")
    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
