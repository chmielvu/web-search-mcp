"""SearchResult data model for multi-search-aggregator."""
from dataclasses import dataclass, asdict, field
from typing import Optional
import urllib.parse


@dataclass
class SearchResult:
    """统一搜索结果数据结构"""

    title: str = ""
    url: str = ""
    summary: str = ""
    source: str = ""
    publish_time: Optional[str] = None
    author: str = ""
    snippet_len: int = 0
    engine_weight: int = 5
    _relevance: float = 0.0  # 查询相关度
    _score: float = 0.0

    def normalize_url(self) -> str:
        """URL 规范化：baidu跳转解析 + 去跟踪参数"""
        url = self.url.strip().rstrip("/")
        if not url or url.startswith('#'):
            return ""
        # 百度跳转链接解析
        if 'baidu.com/link?url=' in url:
            url = urllib.parse.unquote(
                url.split('baidu.com/link?url=')[1].split('&')[0]
            )
        # 去跟踪参数
        if '?' in url:
            base, query = url.split('?', 1)
            track_params = {
                'utm_', 'fbclid', 'gclid', 'clickid', 'ref=', 'share_',
                'eqid', 'from=', 'src=', 'source=', 'spm=',
            }
            params = [
                p for p in query.split('&')
                if p and not any(p.startswith(t) for t in track_params)
            ]
            url = base + ('?' + '&'.join(params) if params else '')
        return url.lower()
