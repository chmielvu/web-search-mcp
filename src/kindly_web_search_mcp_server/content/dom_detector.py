"""DOM complexity signals and Jina route classification.

Static HTML preflight decides the extraction profile before any Jina call:

- ``agent``: simple static prose, fast Reader path.
- ``research``: link-heavy index pages, link preservation plus chunking.
- ``readerlm-v2``: code/table/math structure that needs model-based Markdown.
- ``readerlm-research``: long structural indexes needing both.
- ``research+browser-timing``: dynamic chrome needing render timing.
- ``browser``: JS shells and challenges, handled by the browser stage.

Measurements use a primary WHATWG-conformant :mod:`turbohtml` parse plus its
C block-scoring pass instead of regex markup scanning, so tag-like strings in
script payloads cannot inflate DOM counts; explicit hidden subtrees are excluded
from content measurements. The classifier keeps deterministic, explainable
rules:

- Ketch ``DetectJSShell`` ordering: visible content first; framework roots
  alone never force browser rendering. Browser escalation always pairs low
  content or dominant payload with corroborating shell evidence
  (``noscript_requires_js``, ``empty_mount``, hydration payload ratio).
- turbohtml main-content scoring supplies the positive article evidence
  (content paragraph count, ``article_text_share``/``main_text_share``) that
  protects server-rendered prose from incidental framework and tracker
  signals.
- jusText block heuristics (``MAX_LINK_DENSITY=0.2``, length/stopword bands),
  Lighthouse DOM budgets (body-node warn ``800``, excessive ``1400``), and WDC
  table filtering (row/column scale plus corroboration instead of bare table
  counts) drive the index and structure thresholds. Repeated prose-bearing
  siblings expose listing/forum index pages without treating short navigation
  lists as content.
- JSON-LD ``@type``, RDFa types, and OpenGraph type supply semantic page-type
  evidence (article, product, FAQ, forum, collection) for the prose route.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from typing import Literal

from turbohtml import parse
from turbohtml.extract import boilerplate

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
# Lighthouse warns around 800 body nodes and errors around 1,400.
_DOM_EXCESSIVE = 1400
_DOM_INDEX = 800
# ketch script-to-text gates: 3x corroborated, 8x plus hydration marker.
_SCRIPT_PAYLOAD_HIGH = 8.0
_SCRIPT_PAYLOAD_LOW = 3.0
_SCRIPTS_PER_1K_HIGH = 8.0
_INTERACTIVE_HEAVY = 20
# Positive-prose gates: one dominant semantic container or scored content
# body marks the page as already-usable article text.
_ARTICLE_SHARE = 0.45
_MAIN_SHARE = 0.60
_ARTICLE_SHARE_SELECTOR = 0.70
_MAIN_SHARE_SELECTOR = 0.75

_JS_REQUIRED_RE = re.compile(
    r"(?:enable|requires?|ensure)\s+javascript|javascript\s+is\s+(?:required|disabled)",
    re.IGNORECASE,
)
_CHALLENGE_RE = re.compile(
    r"(?i)(?:just a moment|checking your browser|verify you are human"
    r"|verify you are a human|captcha|cf-chl-|challenge-platform|cloudflare)"
)
_ARTICLE_TYPE_RE = re.compile(
    r"(?i)\b(?:article|newsarticle|blogposting|report|scholarlyarticle|techarticle)\b"
)
_SCHEMA_PAGE_TYPES = frozenset(
    {
        "article",
        "newsarticle",
        "blogposting",
        "scholarlyarticle",
        "techarticle",
        "report",
        "product",
        "product.group",
        "faqpage",
        "qapage",
        "question",
        "answer",
        "discussionforumposting",
        "jobposting",
        "collectionpage",
        "itemlist",
        "softwareapplication",
        "apireference",
    }
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


@dataclass(frozen=True, slots=True)
class TableShape:
    """Row/cell scale of one ``<table>`` element."""

    rows: int
    cells: int
    max_columns: int = 0
    header_cells: int = 0


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
    # Content-topology evidence from turbohtml main-content scoring.
    content_paragraphs: int = 0
    content_char_ratio: float = 0.0
    main_content_text_share: float = 0.0
    article_text_share: float = 0.0
    main_text_share: float = 0.0
    body_requires_js: bool = False
    # Semantic page-type evidence from structured metadata.
    has_article_metadata: bool = False
    schema_page_types: tuple[str, ...] = ()
    # Chrome-restricted link density (nav/footer/aside only).
    nav_link_density: float = 0.0
    # ketch-style shell corroboration; presence alone never forces browser.
    empty_mount: bool = False
    noscript_requires_js: bool = False
    challenge_evidence: bool = False
    # Lighthouse-style shape and page-type extraction evidence.
    body_dom_nodes: int = 0
    dom_depth: int = 0
    max_child_elements: int = 0
    repeated_sibling_items: int = 0


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """Selected Jina route plus the human-readable evidence for it."""

    route: JinaRoute
    reasons: tuple[str, ...]
    signals: DomSignals
    target_selector: str | None = None
    wait_for_selector: str | None = None


def _node_text(node) -> str:
    """Whitespace-collapsed text of a turbohtml node subtree."""
    return " ".join(node.text.split())


def _element_children(node):
    """Return element children while excluding text nodes."""
    return tuple(child for child in node.children if isinstance(getattr(child, "tag", None), str))


def _tree_shape(root) -> tuple[int, int]:
    """Return maximum element depth and direct-child fanout."""
    max_depth = 0
    max_children = 0
    stack = [(child, 1) for child in _element_children(root)]
    while stack:
        node, depth = stack.pop()
        children = _element_children(node)
        max_depth = max(max_depth, depth)
        max_children = max(max_children, len(children))
        stack.extend((child, depth + 1) for child in children)
    return max_depth, max_children


def _repeated_sibling_items(root) -> int:
    """Return the largest repeated, prose-bearing sibling group."""
    max_items = 0
    stack = [root]
    while stack:
        parent = stack.pop()
        children = _element_children(parent)
        stack.extend(children)
        counts: dict[str, int] = {}
        for child in children:
            tag = getattr(child, "tag", None)
            if not isinstance(tag, str) or tag not in {"article", "li", "div"}:
                continue
            text = _node_text(child)
            if len(text.split()) < 15:
                continue
            link_text = sum(len(_node_text(anchor)) for anchor in child.find_all("a"))
            if link_text / max(len(text), 1) > 0.8:
                continue
            counts[tag] = counts.get(tag, 0) + 1
        max_items = max(max_items, max(counts.values(), default=0))
    return max_items if max_items >= 3 else 0


def _marker_hits(lower_html: str) -> tuple[bool, bool]:
    """Detect SPA shell and strong client-render markers."""
    has_spa = any(marker in lower_html for marker in _SPA_MARKERS)
    has_client = any(marker in lower_html for marker in _CLIENT_MARKERS)
    return has_spa, has_client


def _schema_types(metadata_records: object) -> tuple[str, ...]:
    """Collect normalized schema.org types from structured metadata records."""
    types: set[str] = set()

    def visit(record: object) -> None:
        if isinstance(record, (list, tuple)):
            for item in record:
                visit(item)
            return
        if isinstance(record, dict):
            value = record.get("@type")
            nested = record.get("@graph")
        else:
            value = getattr(record, "type", None)
            nested = None
        values = value if isinstance(value, (list, tuple)) else (value,)
        for item in values:
            if isinstance(item, str):
                normalized = item.rsplit("/", 1)[-1].strip().lower()
                if normalized:
                    types.add(normalized)
        if nested is not None:
            visit(nested)

    visit(metadata_records)
    return tuple(sorted(types))


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
        status: Upstream HTTP status; challenge evidence can escalate to browser.
        title: Page title when already known, otherwise parsed from ``html``.
        url: Reserved for future per-domain overrides; currently unused.

    Returns:
        Measured :class:`DomSignals` for :func:`classify_route`.
    """
    _ = url
    source = html or ""
    try:
        doc = parse(source or "<html></html>")
    except (RecursionError, ValueError):
        doc = parse("<html></html>")

    # Capture raw-document evidence before removing non-visible elements.
    raw_dom_nodes = len(doc.select("*"))
    scripts = doc.find_all("script")
    raw_body = doc.find("body")
    if raw_body is None:
        raw_body = doc
    body_dom_nodes = len(raw_body.select("*"))
    dom_depth, max_child_elements = _tree_shape(doc)
    inline_script_chars = sum(len(script.text or "") for script in scripts)
    noscript_text = " ".join(_node_text(node) for node in doc.find_all("noscript"))
    has_spa, has_client = _marker_hits(source.lower())

    resolved_title = title.strip()
    if not resolved_title:
        title_node = doc.find("title")
        resolved_title = _node_text(title_node) if title_node is not None else ""

    structured = None
    with contextlib.suppress(RecursionError, ValueError):
        # A malformed or excessively nested metadata block must not prevent
        # ordinary DOM signals from selecting a route.
        structured = doc.structured_data()
    json_ld = getattr(structured, "json_ld", []) if structured is not None else []
    microdata = getattr(structured, "microdata", []) if structured is not None else []
    rdfa = getattr(structured, "rdfa", []) if structured is not None else []
    schema_types = _schema_types(json_ld) + _schema_types(microdata) + _schema_types(rdfa)
    opengraph = getattr(structured, "opengraph", {}) if structured is not None else {}
    og_value = opengraph.get("og:type") if isinstance(opengraph, dict) else None
    og_type = og_value.strip().lower() if isinstance(og_value, str) else ""
    if og_type:
        schema_types = (*schema_types, og_type.rsplit("/", 1)[-1])
    schema_page_types = tuple(
        page_type for page_type in dict.fromkeys(schema_types) if page_type in _SCHEMA_PAGE_TYPES
    )
    has_article_metadata = bool(_ARTICLE_TYPE_RE.search(" ".join(schema_types)))

    # Remove markup that is not visible page content. The original DOM count,
    # script count, metadata, and raw shell markers remain available above;
    # every content/topology measurement below uses the recovered visible tree.
    doc.remove("script,style,template,noscript,svg,canvas,[hidden],[aria-hidden='true']")
    body = doc.find("body")
    if body is None:
        body = doc
    visible = _node_text(body)
    text_chars = len(visible)

    try:
        paragraph_units = boilerplate(source)
    except (RecursionError, ValueError):
        paragraph_units = []
    content_paragraphs = sum(1 for unit in paragraph_units if not unit.is_boilerplate)
    content_chars = sum(len(unit.text) for unit in paragraph_units if not unit.is_boilerplate)
    content_char_ratio = min(1.0, content_chars / max(text_chars, 1))
    article_nodes = body.find_all("article")
    main_nodes = body.find_all("main")
    try:
        scored_main = doc.main_content()
    except RecursionError:
        scored_main = None
    main_content_text_share = (
        min(1.0, len(_node_text(scored_main)) / text_chars)
        if scored_main is not None and text_chars
        else 0.0
    )

    def _share(nodes) -> float:
        if not nodes or text_chars <= 0:
            return 0.0
        # Largest candidate avoids double-counting nested article/main nodes.
        return min(1.0, max(len(_node_text(node)) for node in nodes) / text_chars)

    article_text_share = _share(article_nodes)
    main_text_share = _share(main_nodes)
    repeated_sibling_items = _repeated_sibling_items(body)

    anchors = body.find_all("a")
    link_count = len(anchors)
    link_text_chars = sum(len(_node_text(anchor)) for anchor in anchors)
    chrome = body.find_all("nav") + body.find_all("footer") + body.find_all("aside")
    chrome_links = [anchor for node in chrome for anchor in node.find_all("a")]
    chrome_text = sum(len(_node_text(node)) for node in chrome)
    chrome_link_text = sum(len(_node_text(anchor)) for anchor in chrome_links)
    nav_link_density = chrome_link_text / max(chrome_text, 1)

    mount_ids = body.select("#root, #app, #__next, #__nuxt, #___gatsby")
    empty_mount = any(not _node_text(node) for node in mount_ids)
    shell_ids = tuple(
        dict.fromkeys(
            value.strip()
            for node in mount_ids
            for value in [node.attrs.get("id")]
            if isinstance(value, str) and value.strip()
        )
    )

    tables: list[TableShape] = []
    for table in body.find_all("table"):
        rows = table.rows()
        widths = [len(row) for row in rows]
        tables.append(
            TableShape(
                rows=len(rows),
                cells=sum(widths),
                max_columns=max(widths, default=0),
                header_cells=len(table.find_all("th")),
            )
        )

    challenge_blob = f"{resolved_title} {noscript_text} {visible[:4000]}"
    lower_source = source.lower()
    return DomSignals(
        status=status,
        title=resolved_title,
        raw_html_chars=len(source),
        body_text_chars=text_chars,
        dom_nodes=raw_dom_nodes,
        script_count=len(scripts),
        inline_script_chars=inline_script_chars,
        pre_count=len(body.find_all("pre")),
        code_count=len(body.find_all("code")),
        tables=tuple(tables),
        heading_count=len(body.select("h1,h2,h3,h4,h5,h6")),
        list_count=len(body.select("ul,ol")),
        list_item_count=len(body.find_all("li")),
        iframe_count=len(body.find_all("iframe")),
        button_count=len(body.find_all("button")),
        form_count=len(body.find_all("form")),
        interactive_count=len(body.select("input,select,textarea"))
        + len(body.select('[aria-expanded="true"]')),
        lazy_attr_hits=len(body.select("[loading=lazy]"))
        + len(body.select("[data-src]"))
        + len(body.select("[data-lazy-src]")),
        shell_ids=shell_ids,
        link_count=link_count,
        link_text_chars=link_text_chars,
        link_density=(link_text_chars / max(text_chars, 1)) if text_chars else 0.0,
        has_article=bool(article_nodes),
        has_main=bool(main_nodes),
        has_math=bool(body.find_all("math"))
        or "katex" in lower_source
        or "mathjax" in lower_source,
        has_spa_marker=has_spa,
        has_client_marker=has_client,
        content_paragraphs=content_paragraphs,
        content_char_ratio=content_char_ratio,
        main_content_text_share=main_content_text_share,
        article_text_share=article_text_share,
        main_text_share=main_text_share,
        body_requires_js=bool(_JS_REQUIRED_RE.search(visible)),
        has_article_metadata=has_article_metadata,
        schema_page_types=schema_page_types,
        nav_link_density=nav_link_density,
        empty_mount=empty_mount,
        challenge_evidence=bool(_CHALLENGE_RE.search(challenge_blob) or "cf-chl-" in lower_source),
        body_dom_nodes=body_dom_nodes,
        dom_depth=dom_depth,
        max_child_elements=max_child_elements,
        repeated_sibling_items=repeated_sibling_items,
    )


def _data_tables(signals: DomSignals) -> tuple[TableShape, ...]:
    """Tables large enough to be relational data rather than layout."""
    return tuple(
        shape
        for shape in signals.tables
        if shape.rows >= 3 and shape.max_columns >= 3 and shape.cells >= 9
    )


def _large_tables(signals: DomSignals) -> tuple[TableShape, ...]:
    """Tables large enough to dominate extraction quality when genuine."""
    return tuple(shape for shape in signals.tables if shape.rows >= 5 and shape.cells >= 15)


def classify_route(signals: DomSignals) -> RouteDecision:
    """Select the Jina route for measured DOM signals.

    Precedence follows Ketch ``DetectJSShell``: HTTP and challenge evidence
    first, then content-poor shell corroboration, then the high-text
    client-render override, then long structural indexes, index pressure,
    structure, dynamic chrome, and finally the fast path with positive prose
    evidence. Framework roots alone never force the browser route.

    Args:
        signals: Measured :class:`DomSignals` from :func:`extract_dom_signals`.

    Returns:
        :class:`RouteDecision` with the route and its evidence trail.
    """
    text = max(signals.body_text_chars, 1)
    payload_ratio = signals.inline_script_chars / text
    dom_nodes = signals.body_dom_nodes or signals.dom_nodes

    def decide(route: JinaRoute, *reasons: str) -> RouteDecision:
        selector = _suggested_selector(signals)
        wait = selector if route in {"browser", "research+browser-timing"} else None
        return RouteDecision(
            route=route,
            reasons=tuple(reasons),
            signals=signals,
            target_selector=selector,
            wait_for_selector=wait,
        )

    if signals.status in {401, 403, 429} and signals.challenge_evidence:
        return decide("browser", f"http_{signals.status}", "challenge")
    if signals.status >= 500 and signals.body_text_chars < 200:
        return decide("browser", f"http_{signals.status}", "empty-server-error")
    if signals.challenge_evidence and signals.body_text_chars < 1200:
        return decide("browser", "challenge-evidence")

    # Ketch-style shell logic: low content plus a corroborator. Framework
    # roots alone are insufficient; SSR pages ship full content inside them.
    low_content = signals.body_text_chars < 200 and signals.content_paragraphs <= 2
    if low_content and (
        signals.noscript_requires_js
        or signals.body_requires_js
        or signals.empty_mount
        or signals.has_spa_marker
        or payload_ratio > _SCRIPT_PAYLOAD_LOW
    ):
        return decide("browser", "low-text-js-shell")

    data_tables = _data_tables(signals)
    large_tables = _large_tables(signals)
    # A lone chapter-index/TOC table without code or math is navigation, not
    # structure (verified on Project Gutenberg: 102x204 TOC, zero scripts).
    lonely_index_table = (
        len(data_tables) == 1
        and signals.code_count < 2
        and signals.pre_count < 1
        and not signals.has_math
        and (signals.body_text_chars > 50000 or dom_nodes > 3000)
    )
    if lonely_index_table:
        data_tables = ()
        large_tables = ()

    # Structural scoring replaces threshold cliffs: two independent
    # corroborating families justify ReaderLM, one moderate signal too.
    structural_score = 0
    structural_reasons: list[str] = []
    if signals.pre_count >= 5 or signals.code_count >= 10:
        structural_score += 2
        structural_reasons.append(f"code:{signals.code_count}/pre:{signals.pre_count}")
    elif signals.pre_count >= 2 or signals.code_count >= 5:
        structural_score += 1
    if len(data_tables) >= 2:
        structural_score += 2
        structural_reasons.append(f"tables:{len(data_tables)}")
    elif len(data_tables) == 1:
        structural_score += 1
    if signals.has_math:
        structural_score += 1
        structural_reasons.append("math")
    if len(large_tables) >= 1 and len(data_tables) >= 2:
        structural_score += 1

    strong_structural = structural_score >= 2
    weak_structural = structural_score == 1

    # Positive article evidence protects prose pages from incidental
    # framework, tracker, and navigation signals (refined-detector gates).
    readerable_prose = (
        signals.article_text_share >= _ARTICLE_SHARE
        or signals.main_text_share >= _MAIN_SHARE
        or signals.main_content_text_share >= _ARTICLE_SHARE
        or signals.has_article_metadata
        or (signals.content_paragraphs >= 5 and signals.content_char_ratio >= 0.4)
    )

    index_pressure = (
        dom_nodes > _DOM_MASSIVE
        or signals.link_count > _LINKS_MASSIVE
        or (signals.link_density > _LINK_DENSITY_INDEX and dom_nodes > _DOM_INDEX)
    )

    # Ketch high-text override: a streaming/hydration payload dwarfing visible
    # text proves client-rendered content even when fallback text exists.
    if signals.body_text_chars >= 200 and (
        payload_ratio > _SCRIPT_PAYLOAD_HIGH and signals.has_client_marker
    ):
        # Readerable prose with dominant hydration payload still takes the
        # direct engine: SSR news pages ship huge tracking payloads that the
        # Reader strips, and the browser engine would only add latency.
        if index_pressure and not readerable_prose:
            return decide(
                "research+browser-timing",
                f"client-render:{payload_ratio:.1f}x-plus-marker",
            )
        if not readerable_prose:
            return decide("browser", f"client-render:{payload_ratio:.1f}x-plus-marker")
        return decide("agent", "readerable-prose-overrides-client-render")

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
    if signals.iframe_count >= 2:
        dynamic.append(f"iframes:{signals.iframe_count}")
    is_dynamic = (dom_nodes > _DOM_EXCESSIVE and bool(dynamic)) or len(dynamic) >= 2

    if (dom_nodes > _DOM_MASSIVE or signals.link_count > _LINKS_MASSIVE) and (
        strong_structural or len(data_tables) >= 2 or len(large_tables) >= 2
    ):
        return decide("readerlm-research", "long-structural-index")
    if index_pressure and is_dynamic:
        return decide("research+browser-timing", f"index-plus-dynamic:{' '.join(dynamic)}")
    if index_pressure:
        return decide("research", "index-pressure")
    if strong_structural:
        return decide("readerlm-v2", "strong-structural", *structural_reasons)
    if is_dynamic:
        return decide("research+browser-timing", f"dynamic:{' '.join(dynamic)}")
    if weak_structural:
        return decide("readerlm-v2", "weak-structural", *structural_reasons)
    has_index_metadata = any(
        page_type in {"collectionpage", "itemlist", "discussionforumposting", "qapage"}
        for page_type in signals.schema_page_types
    )
    if signals.repeated_sibling_items >= 3 and (signals.link_count >= 5 or has_index_metadata):
        return decide("research", f"repeated-items:{signals.repeated_sibling_items}")
    if signals.link_density > _LINK_DENSITY_HEAVY or signals.link_count > _LINKS_HEAVY:
        return decide("research", "link-heavy")
    if readerable_prose:
        return decide("agent", "readerable-prose")
    scripts_per_1k = signals.script_count / (text / 1000)
    if scripts_per_1k > _SCRIPTS_PER_1K_HIGH and not dynamic:
        # Tracker-heavy prose (verified: 64 scripts over 24k chars) stays fast;
        # raw script counts alone do not prove dynamic rendering.
        return decide("agent", f"tracker-scripts:{scripts_per_1k:.1f}-per-1k")
    return decide("agent", "simple")


def _suggested_selector(signals: DomSignals) -> str | None:
    """Suggest a Jina target selector when one container clearly dominates.

    Exposed as a recommendation for the caller, never forced: selector
    mistakes silently remove useful content.
    """
    if signals.article_text_share >= _ARTICLE_SHARE_SELECTOR:
        return "article"
    if signals.main_text_share >= _MAIN_SHARE_SELECTOR:
        return "main"
    return None


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
