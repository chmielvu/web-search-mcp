from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .content import fetch_payload
from .files import write_json_atomic, write_text_atomic
from .search_web import fetch_web_search_payload


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _unique_urls(results: list[dict[str, Any]], limit: int) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for result in results:
        url = result.get("link") or result.get("url")
        if not isinstance(url, str) or not url.strip() or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def _report_markdown(
    query: str,
    results: list[dict[str, Any]],
    output_dir: Path,
) -> str:
    lines = [
        f"# Research Collection: {query}",
        "",
        "This report contains deterministic search results and fetched source artifacts. It is not an AI synthesis.",
        "",
        "## Sources",
        "",
    ]
    for index, result in enumerate(results, start=1):
        url = result.get("url") or ""
        url = url if isinstance(url, str) else str(url)
        derived_title = (
            url.rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title()
        )
        title = result.get("title") or derived_title or url or "Untitled source"
        artifact = result.get("artifact_path") or ""
        lines.append(f"{index}. [{title}]({url}) — `{result.get('status', 'unknown')}`")
        if artifact:
            lines.append(f"   - Local artifact: [{Path(artifact).name}]({Path(artifact).name})")
        error_obj = result.get("error")
        if isinstance(error_obj, dict):
            lines.append(
                f"   - Error: {error_obj.get('message', error_obj.get('code', 'unknown'))}"
            )
            if error_obj.get("resolution"):
                lines.append(f"   - Resolution: {error_obj['resolution']}")
        status = result.get("status")
        if status in {"login", "paywall", "bot", "js_shell"}:
            lines.append(
                f"   - Access signal: {status} (page body may be the wall, not the content)"
            )
    lines += [
        "",
        "## Collection metadata",
        "",
        f"- Generated: `{_utc_now()}`",
        f"- Output directory: `{output_dir}`",
    ]
    return "\n".join(lines) + "\n"


async def collect_research_bundle(
    query: str,
    research_goal: str,
    *,
    output_dir: str | Path,
    top_results: int = 5,
    rewrite: bool = True,
    ai_summary: bool = False,
) -> dict[str, Any]:
    if top_results < 1:
        raise ValueError("top_results must be >= 1")
    if not query.strip() or not research_goal.strip():
        raise ValueError("query and research_goal must be non-blank")

    root = Path(output_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    search_payload = await fetch_web_search_payload(
        query,
        rewrite=rewrite,
        research_goal=research_goal,
        diagnostics=True,
    )
    selected = list(search_payload.get("results") or [])[:top_results]
    urls = _unique_urls(selected, top_results)
    content_payload = await fetch_payload(
        urls=urls,
        cursor=None,
        offset=0,
        ai_summary=ai_summary,
        focus_query=research_goal,
        include_links=True,
    )

    results: list[dict[str, Any]] = []
    for index, raw in enumerate(content_payload.get("results") or [], start=1):
        result = dict(raw)
        content = result.pop("content", "")
        artifact_path = root / "sources" / f"source-{index:03d}.md"
        write_text_atomic(artifact_path, content if isinstance(content, str) else str(content))
        result["artifact_path"] = str(artifact_path)
        results.append(result)

    write_json_atomic(root / "search.json", search_payload)
    write_json_atomic(root / "sources.json", {**content_payload, "results": results})
    report_path = root / "report.md"
    write_text_atomic(report_path, _report_markdown(query, results, root))

    manifest = {
        "schema_version": "1.0",
        "kind": "research_collection",
        "created_at": _utc_now(),
        "query": query,
        "research_goal": research_goal,
        "rewrite": rewrite,
        "top_results": top_results,
        "requested_urls": urls,
        "files": {
            "search": str(root / "search.json"),
            "sources": str(root / "sources.json"),
            "report": str(report_path),
        },
        "source_count": len(results),
        "partial": bool(content_payload.get("has_more"))
        or any(item.get("status") != "success" for item in results),
    }
    manifest_path = write_json_atomic(root / "manifest.json", manifest)
    return {
        "kind": "research_collection",
        "query": query,
        "research_goal": research_goal,
        "output_dir": str(root),
        "manifest_path": manifest_path,
        "report_path": str(report_path),
        "source_count": len(results),
        "partial": manifest["partial"],
        "run_key": search_payload.get("run_key"),
    }
