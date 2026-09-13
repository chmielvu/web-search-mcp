"""DOM complexity signals and Jina route classification.

Static HTML preflight decides the extraction profile before any Jina call:

- ``agent``: simple static prose, fast Reader path.
- ``research``: link-heavy index pages, link preservation plus chunking.
- ``readerlm-v2``: code/table/math structure that needs model-based Markdown.
- ``readerlm-research``: long structural indexes needing both.
- ``research+browser-timing``: dynamic chrome needing render timing.
- ``browser``: JS shells and challenges, handled by the browser stage.

Thresholds follow ready-made solutions: jusText block heuristics
(``MAX_LINK_DENSITY=0.2``, length/stopword bands), ketch ``DetectJSShell``
(script-to-text ``3x`` low-text / ``8x`` plus hydration marker), Lighthouse
DOM budgets (warn ``800``, excessive ``1400``), and WDC table filtering
(row/column scale plus corroboration instead of bare table counts).
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from typing import Literal

JinaRoute = Literal[
    "agent",
    "research",
    "readerlm-v2",
    "readerlm-research",
    "research+browser-timing",
    "browser",
]

# Page-level link density runs hotter than jusText's 0.2 block gate because
# navigation chrome dilutes across the whole document.
_LINK_DENSITY_INDEX = 0.30
_LINK_DENSITY_HEAVY = 0.25
_DOM_MASSIVE = 8000
_LINKS_MASSIVE = 1000
_LINKS_HEAVY = 150
_DOM_EXCESSIVE = 1400
_DOM_INDEX = 1000
# ketch script-to-text gates: 3x corroborated, 8x plus hydration marker.
_SCRIPT_PAYLOAD_HIGH = 8.0
_SCRIPT_PAYLOAD_LOW = 3.0
_SCRIPTS_PER_1K_HIGH = 8.0
_INTERACTIVE_HEAVY = 20
_LAZY_HEAVY = 50

_SCRIPT_RE = re.compile(r"(?is)<script\b[^>]*>.*?</script>")
_STYLE_RE = re.compile(r"(?is)<style\b[^>]*>.*?</style>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
_ANCHOR_RE = re.compile(r"(?is)<a\b[^>]*>(.*?)</a>")
_TABLE_RE = re.compile(r"(?is)<table\b[^>]*>(.*?)</table>")
_TR_RE = re.compile(r"(?is)<tr\b")
_CELL_RE = re.compile(r"(?is)<(?:td|th)\b")
_PRE_RE = re.compile(r"(?is)<pre\b")
_CODE_RE = re.compile(r"(?is)<code\b")
_HEADING_RE = re.compile(r"(?is)<h[1-6]\b")
_LIST_RE = re.compile(r"(?is)<(?:ul|ol)\b")
_LI_RE = re.compile(r"(?is)<li\b")
_IFRAME_RE = re.compile(r"(?is)<iframe\b")
_BUTTON_RE = re.compile(r"(?is)<button\b")
_FORM_RE = re.compile(r"(?is)<form\b")
_INTERACTIVE_RE = re.compile(r"(?is)<(?:input|select|textarea)\b|aria-expanded\s*=\s*[\"']true[\"']")
_LAZY_RE = re.compile(
    r"(?i)(?:data-(?:src|lazy|load)|loading\s*=|aria-expanded\s*=|load more|transcript)"
)
_ID_RE = re.compile(r"(?i)\bid=[\"']([^\"']+)[\"']")
_ARTICLE_RE = re.compile(r"(?is)<article\b")
_MAIN_RE = re.compile(r"(?is)<main\b")
_MATH_RE = re.compile(r"(?is)(?:<math\b|katex|mathjax)")
_CHALLENGE_RE = re.compile(
    r"(?i)(?:just a moment|checking your browser|verify you are human"
    r"|enable javascript|requires javascript|javascript is required"
    r"|javascript is disabled|cloudflare)"
)

# ketch spaMarkers, lowercased substring signals for framework shells.
_SPA_MARKERS = (
    'id="__next"',
    "id='__next'",
    'id="__nuxt"',
    "id='__nuxt'",
    "data-reactroot",
    "ng-version=",
    "<app-root",
    'id="___gatsby"',
    "id='___gatsby'",
    "__next_data__",
    "__nuxt__",
    "__next_f",
    'id="_r_"',
    "id='_r_'",
    "data-v-app",
    "data-svelte",
    "__sveltekit",
    "q:container",
    "astro-island",
)

# ketch clientRenderMarkers, the strong subset proving page content itself is
# client-rendered rather than merely framed by a hydrated shell.
_CLIENT_MARKERS = (
    "__next_f",
    "data-v-app",
    "__sveltekit",
    "data-svelte",
    "q:container",
    "astro-island",
    "<!--$-->",
    '<div id="root"></div>',
    "<div id='root'></div>",
    '<div id="app"></div>',
    "<div id='app'></div>",
)

_SHELL_IDS = frozenset({"root", "app", "__next", "__nuxt", "___gatsby", "gatsby-focus-wrapper"})


@dataclass(frozen=True, slots=True)
class TableShape:
    """Row/cell scale of one ``<table>`` element."""

    rows: int
    cells: int


@dataclass(frozen=True, slots=True)
class DomSignals:
    """Measurable static-HTML features driving route selection."""

    status: int
    title: str
    raw_html_chars: int
    body_text_chars: int
    dom_nodes: int
    script_count: int
    inline_script_chars: int
    pre_count: int
    code_count: int
    tables: tuple[TableShape, ...]
    heading_count: int
    list_count: int
    list_item_count: int
    iframe_count: int
    button_count: int
    form_count: int
    interactive_count: int
    lazy_attr_hits: int
    shell_ids: tuple[str, ...]
    link_count: int
    link_text_chars: int
    link_density: float
    has_article: bool
    has_main: bool
    has_math: bool
    has_spa_marker: bool
    has_client_marker: bool


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """Selected Jina route plus the human-readable evidence for it."""

    route: JinaRoute
    reasons: tuple[str, ...]
    signals: DomSignals


def _strip_to_text(html: str) -> str:
    """Return collapsed visible text with scripts, styles, and tags removed."""
    cleaned = _SCRIPT_RE.sub(" ", html)
    cleaned = _STYLE_RE.sub(" ", cleaned)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = html_lib.unescape(cleaned)
    return _WS_RE.sub(" ", cleaned).strip()


def _table_shapes(html: str) -> tuple[TableShape, ...]:
    """Measure row/cell scale for every table element."""
    shapes: list[TableShape] = []
    for match in _TABLE_RE.finditer(html):
        body = match.group(1)
        shapes.append(
            TableShape(
                rows=len(_TR_RE.findall(body)),
                cells=len(_CELL_RE.findall(body)),
            )
        )
    return tuple(shapes)


def _link_stats(html: str) -> tuple[int, int]:
    """Return ``(anchor count, anchor visible-text chars)``."""
    count = 0
    chars = 0
    for match in _ANCHOR_RE.finditer(html):
        count += 1
        chars += len(_strip_to_text(match.group(1)))
    return count, chars


def _marker_hits(lower_html: str) -> tuple[bool, bool]:
    """Detect SPA shell and strong client-render markers."""
    has_spa = any(marker in lower_html for marker in _SPA_MARKERS)
    has_client = any(marker in lower_html for marker in _CLIENT_MARKERS)
    return has_spa, has_client


def extract_dom_signals(
    html: str,
    *,
    status: int = 200,
    title: str = "",
    url: str = "",
) -> DomSignals:
    """Measure classification signals from raw HTML.

    Args:
        html: Raw page source, any length; empty HTML yields zero signals.
        status: Upstream HTTP status; non-200 pages route to the browser.
        title: Page title when already known, otherwise parsed from ``html``.
        url: Reserved for future per-domain overrides; currently unused.

    Returns:
        Measured :class:`DomSignals` for :func:`classify_route`.
    """
    _ = url
    source = html or ""
    lower = source.lower()
    text = _strip_to_text(source)
    link_count, link_text_chars = _link_stats(source)
    tables = _table_shapes(source)
    shell_ids = tuple(
        dict.fromkeys(
            value
            for value in _ID_RE.findall(source)
            if value.strip().lower() in _SHELL_IDS
        )
    )
    has_spa, has_client = _marker_hits(lower)
    resolved_title = title.strip()
    if not resolved_title:
        match = _TITLE_RE.search(source)
        if match:
            resolved_title = _WS_RE.sub(" ", html_lib.unescape(match.group(1))).strip()
    return DomSignals(
        status=status,
        title=resolved_title,
        raw_html_chars=len(source),
        body_text_chars=len(text),
        dom_nodes=len(re.findall(r"<[a-z][^>]*>", source, re.IGNORECASE)),
        script_count=len(re.findall(r"<script\b", source, re.IGNORECASE)),
        inline_script_chars=sum(len(block) for block in _SCRIPT_RE.findall(source)),
        pre_count=len(_PRE_RE.findall(source)),
        code_count=len(_CODE_RE.findall(source)),
        tables=tables,
        heading_count=len(_HEADING_RE.findall(source)),
        list_count=len(_LIST_RE.findall(source)),
        list_item_count=len(_LI_RE.findall(source)),
        iframe_count=len(_IFRAME_RE.findall(source)),
        button_count=len(_BUTTON_RE.findall(source)),
        form_count=len(_FORM_RE.findall(source)),
        interactive_count=len(_INTERACTIVE_RE.findall(source)),
        lazy_attr_hits=len(_LAZY_RE.findall(source)),
        shell_ids=shell_ids,
        link_count=link_count,
        link_text_chars=link_text_chars,
        link_density=(link_text_chars / max(len(text), 1)) if text else 0.0,
        has_article=bool(_ARTICLE_RE.search(source)),
        has_main=bool(_MAIN_RE.search(source)),
        has_math=bool(_MATH_RE.search(source)),
        has_spa_marker=has_spa,
        has_client_marker=has_client,
    )


def _data_tables(signals: DomSignals) -> tuple[TableShape, ...]:
    """Tables large enough to be relational data rather than layout."""
    return tuple(
        shape for shape in signals.tables if shape.rows >= 3 and shape.cells >= 9
    )


def _large_tables(signals: DomSignals) -> tuple[TableShape, ...]:
    """Tables large enough to dominate extraction quality when genuine."""
    return tuple(
        shape for shape in signals.tables if shape.rows >= 5 and shape.cells >= 15
    )


def classify_route(signals: DomSignals) -> RouteDecision:
    """Select the Jina route for measured DOM signals.

    Precedence is deliberate: challenge/shell markers first, then the ketch
    high-text client-render override, then long structural indexes, then
    index pressure, structure, dynamic chrome, and finally the fast path.

    Args:
        signals: Measured :class:`DomSignals` from :func:`extract_dom_signals`.

    Returns:
        :class:`RouteDecision` with the route and its evidence trail.
    """
    text = max(signals.body_text_chars, 1)
    payload_ratio = signals.inline_script_chars / text

    data_tables = _data_tables(signals)
    large_tables = _large_tables(signals)
    # A lone chapter-index/TOC table without code or math is navigation, not
    # structure (verified on Project Gutenberg: 102x204 TOC, zero scripts).
    lonely_index_table = (
        len(data_tables) == 1
        and signals.code_count < 2
        and signals.pre_count < 1
        and not signals.has_math
        and (signals.body_text_chars > 50000 or signals.dom_nodes > 3000)
    )
    if lonely_index_table:
        data_tables = ()
        large_tables = ()

    strong_structural = (
        signals.code_count >= 10
        or signals.pre_count >= 5
        or len(data_tables) >= 2
        or (
            len(large_tables) >= 1
            and (
                signals.code_count >= 5
                or signals.pre_count >= 1
                or signals.has_math
                or len(data_tables) >= 2
            )
        )
    )
    weak_structural = (
        signals.pre_count >= 2
        or signals.code_count >= 5
        or len(data_tables) == 1
        or signals.has_math
    )

    def decide(route: JinaRoute, *reasons: str) -> RouteDecision:
        return RouteDecision(route=route, reasons=tuple(reasons), signals=signals)

    if signals.status != 200:
        return decide("browser", f"http_{signals.status}")
    if _CHALLENGE_RE.search(signals.title) or (
        signals.body_text_chars < 200 and signals.script_count >= 1
    ):
        return decide("browser", "challenge-or-shell")
    if signals.shell_ids:
        return decide("browser", f"spa-root:{','.join(signals.shell_ids)}")
    if signals.body_text_chars < 500 and signals.script_count >= 5:
        return decide("browser", "low-text-high-script")

    index_pressure = (
        signals.dom_nodes > _DOM_MASSIVE
        or signals.link_count > _LINKS_MASSIVE
        or (signals.link_density > _LINK_DENSITY_INDEX and signals.dom_nodes > _DOM_INDEX)
    )

    # Ketch high-text override: a streaming/hydration payload dwarfing visible
    # text proves client-rendered content even when fallback text exists.
    if signals.body_text_chars >= 200 and (
        payload_ratio > _SCRIPT_PAYLOAD_HIGH and signals.has_client_marker
    ):
        if index_pressure:
            return decide(
                "research+browser-timing",
                f"client-render:{payload_ratio:.1f}x-plus-marker",
            )
        return decide("browser", f"client-render:{payload_ratio:.1f}x-plus-marker")

    dynamic: list[str] = []
    if payload_ratio > _SCRIPT_PAYLOAD_HIGH:
        dynamic.append(f"script-payload:{payload_ratio:.1f}x")
    elif payload_ratio > _SCRIPT_PAYLOAD_LOW and (
        signals.body_text_chars < 2000 or signals.interactive_count > 10
    ):
        dynamic.append(f"script-payload:{payload_ratio:.1f}x-corroborated")
    if signals.has_client_marker and payload_ratio > _SCRIPT_PAYLOAD_LOW:
        dynamic.append("hydration-marker-plus-payload")
    if signals.interactive_count > _INTERACTIVE_HEAVY:
        dynamic.append(f"interactive:{signals.interactive_count}")
    if signals.lazy_attr_hits > _LAZY_HEAVY:
        dynamic.append(f"lazy:{signals.lazy_attr_hits}")
    if signals.iframe_count >= 2:
        dynamic.append(f"iframes:{signals.iframe_count}")
    is_dynamic = (signals.dom_nodes > _DOM_EXCESSIVE and bool(dynamic)) or len(dynamic) >= 2

    if (signals.dom_nodes > _DOM_MASSIVE or signals.link_count > _LINKS_MASSIVE) and (
        strong_structural or len(data_tables) >= 2 or len(large_tables) >= 2
    ):
        return decide("readerlm-research", "long-structural-index")
    if index_pressure and is_dynamic:
        return decide("research+browser-timing", f"index-plus-dynamic:{' '.join(dynamic)}")
    if index_pressure:
        return decide("research", "index-pressure")
    if strong_structural:
        return decide("readerlm-v2", "strong-structural")
    if is_dynamic:
        return decide("research+browser-timing", f"dynamic:{' '.join(dynamic)}")
    if weak_structural:
        return decide("readerlm-v2", "weak-structural")
    if signals.link_density > _LINK_DENSITY_HEAVY or signals.link_count > _LINKS_HEAVY:
        return decide("research", "link-heavy")
    scripts_per_1k = signals.script_count / (text / 1000)
    if scripts_per_1k > _SCRIPTS_PER_1K_HIGH and not dynamic:
        # Tracker-heavy prose (verified: 64 scripts over 24k chars) stays fast;
        # raw script counts alone do not prove dynamic rendering.
        return decide("agent", f"tracker-scripts:{scripts_per_1k:.1f}-per-1k")
    return decide("agent", "simple")


def analyze_html(
    html: str,
    *,
    status: int = 200,
    title: str = "",
    url: str = "",
) -> RouteDecision:
    """Measure HTML and classify it in one call.

    Args:
        html: Raw page source.
        status: Upstream HTTP status for the page.
        title: Optional pre-parsed title override.
        url: Reserved per-domain override hook.

    Returns:
        :class:`RouteDecision` for the page.
    """
    return classify_route(extract_dom_signals(html, status=status, title=title, url=url))
