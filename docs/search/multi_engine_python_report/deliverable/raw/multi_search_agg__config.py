"""Configuration loader: config.yaml + environment variables."""
import os
import yaml

# Path to the project root directory
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_config():
    """Load config.yaml from project root, falls back to defaults."""
    config_path = os.path.join(_PROJECT_DIR, "config.yaml")
    cfg = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            pass
    return cfg


_CONFIG = _load_config()

# --- Paths ---
WORKSPACE = os.path.expanduser(_CONFIG.get("workspace", "./workspace"))

# --- Timing ---
TIMEOUT = int(_CONFIG.get("timeout", 35))
MAX_WORKERS = int(_CONFIG.get("max_workers", 40))
RETRY_DELAY = float(_CONFIG.get("retry_delay", 0.5))

# --- Output ---
OUTPUT_DIR = os.path.expanduser(
    _CONFIG.get("output_dir", os.path.join(WORKSPACE, "search_agg"))
)

# --- API keys (from environment, never from config file) ---
BAILIAN_KEY = os.environ.get("BAILIAN_API_KEY", "")
ZHIPU_KEY = os.environ.get("ZHIPU_API_KEY", "")
ZAI_KEY = os.environ.get("ZAI_API_KEY", "")
SERPBASE_KEY = os.environ.get("SERPBASE_API_KEY", "")
AMINER_KEY = os.environ.get("AMINER_API_KEY", "")
ZHIHU_KEY = os.environ.get("ZHIHU_ACCESS_SECRET", "")
OPENCLI_PATH = os.environ.get("OPENCLI_PATH", "opencli")

# --- Engine weights ---
DEFAULT_WEIGHTS = {
    # API/MCP 直连
    "智谱Pro": 9, "智谱Sogou": 8, "智谱Quark": 7,
    "Z.AI": 9, "百炼": 9,
    "SerpBase Google": 10,
    # opencli 轻量
    "Hacker News": 7, "Stack Overflow": 8, "Wikipedia": 5,
    "arXiv": 8, "OpenAlex": 6, "OpenReview": 6, "DBLP": 6,
    "npm": 5, "crates.io": 5, "MDN": 6,
    # opencli 重型
    "Substack": 5, "微信读书": 5,
    "Google": 10, "知乎": 8, "Reddit": 7, "小红书": 5,
    # web_fetch
    "Bing国内": 7, "Bing国际": 8, "360搜索": 6, "搜狗Web": 6,
    "微信搜一搜": 6,
    "Startpage": 6, "Brave_WF": 6, "Qwant": 5,
    # MCP stdio
    "EnhancedBing": 8,
    # Skill
    "AMiner论文": 7, "AMiner专利": 7, "知乎Skill": 7,
}

WEIGHTS = {}
cfg_weights = _CONFIG.get("weights", {})
for name, w in DEFAULT_WEIGHTS.items():
    WEIGHTS[name] = cfg_weights.get(name, w)


def get_engine_weight(name: str) -> int:
    """Get the weight for a named engine."""
    return WEIGHTS.get(name, 5)


def is_engine_enabled(engine_id: str) -> bool:
    """Check if an engine is enabled via config overrides."""
    overrides = _CONFIG.get("engine_overrides") or {}
    override = overrides.get(engine_id, {})
    return override.get("enabled", True)


# ============================================================
# 服务器配置
# ============================================================
def _server_config():
    sc = _CONFIG.get("server", {})
    return {
        "host": sc.get("host", "0.0.0.0"),
        "port": int(os.environ.get("SEARCH_PORT", sc.get("port", 8200))),
        "cors_origins": sc.get("cors_origins", ["*"]),
    }


SERVER = _server_config()
SERVER_HOST = SERVER["host"]
SERVER_PORT = SERVER["port"]
SERVER_CORS_ORIGINS = SERVER["cors_origins"]


def _search_config():
    sc = _CONFIG.get("search", {})
    return {
        "default_top_k": int(sc.get("default_top_k", 50)),
        "max_top_k": int(sc.get("max_top_k", 200)),
        "default_timeout": int(sc.get("default_timeout", 35)),
        "max_workers": int(sc.get("max_workers", 40)),
    }


SEARCH_DEFAULTS = _search_config()
DEFAULT_TOP_K = SEARCH_DEFAULTS["default_top_k"]
MAX_TOP_K = SEARCH_DEFAULTS["max_top_k"]
DEFAULT_TIMEOUT = SEARCH_DEFAULTS["default_timeout"]
SEARCH_MAX_WORKERS = SEARCH_DEFAULTS["max_workers"]
