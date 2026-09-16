"""Source registry."""

from .arxiv import ArxivSource
from .baidu import BaiduSource
from .base import Source
from .bilibili import BilibiliSource
from .crossref import CrossrefSource
from .exa import ExaSource
from .facebook import FacebookSource
from .github import GitHubSource
from .hackernews import HackerNewsSource
from .instagram import InstagramSource
from .linkedin import LinkedInSource
from .openalex import OpenAlexSource
from .reddit import RedditSource
from .searxng import SearXNGSource
from .semantic_scholar import SemanticScholarSource
from .stackoverflow import StackOverflowSource
from .toutiao import ToutiaoSource
from .twitter import TwitterSource
from .v2ex import V2EXSource
from .web import WebSource
from .weibo import WeiboSource
from .wikipedia import WikipediaSource
from .xiaohongshu import XiaohongshuSource
from .youtube import YouTubeSource
from .zhihu import ZhihuSource
from .zhihu_hot import ZhihuHotSource

ALL_SOURCES: dict[str, Source] = {
    s.name: s
    for s in (
        SearXNGSource(),
        ExaSource(),
        TwitterSource(),
        RedditSource(),
        GitHubSource(),
        YouTubeSource(),
        FacebookSource(),
        InstagramSource(),
        BilibiliSource(),
        LinkedInSource(),
        V2EXSource(),
        XiaohongshuSource(),
        WebSource(),
        ArxivSource(),
        OpenAlexSource(),
        CrossrefSource(),
        StackOverflowSource(),
        SemanticScholarSource(),
        HackerNewsSource(),
        WikipediaSource(),
        # Chinese-ecosystem tier (v0.4, gated by KORTEX_SEARCH_CN_SOURCES)
        ZhihuSource(),
        ZhihuHotSource(),
        WeiboSource(),
        BaiduSource(),
        ToutiaoSource(),
    )
}


def get_sources(names: list[str]) -> list[Source]:
    return [ALL_SOURCES[n] for n in names if n in ALL_SOURCES]


def valid_names() -> list[str]:
    return sorted(ALL_SOURCES.keys())
