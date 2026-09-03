from __future__ import annotations

import asyncio

from collections.abc import Iterator
from pathlib import Path

import pytest

from kindly_web_search_mcp_server.tools.code_search.exploration import code_fetch
from kindly_web_search_mcp_server.tools.code_search.snapshot import (
    SnapshotManager,
    reset_snapshot_manager_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_snapshots() -> Iterator[None]:
    reset_snapshot_manager_for_tests(None)
    yield
    reset_snapshot_manager_for_tests(None)


def _seed_snapshot(tmp_path: Path) -> SnapshotManager:
    source = tmp_path / "repo"
    (source / "src").mkdir(parents=True)
    (source / "src" / "auth.py").write_text(
        "def login():\n    return True\n\ndef authenticate():\n    return login()\n",
        encoding="utf-8",
    )
    (source / "README.md").write_text("# demo repository\n", encoding="utf-8")
    manager = SnapshotManager(
        db_path=str(tmp_path / "snap.sqlite"),
        worktree_root=tmp_path / "worktrees",
    )
    manager.build_from_directory("owner/repo", "main", "a" * 40, source)
    reset_snapshot_manager_for_tests(manager)
    return manager


@pytest.mark.asyncio
async def test_code_fetch_rejects_invalid_repository() -> None:
    result = await code_fetch("not-a-repository", ctx=None)
    assert result.outcome == "error"
    assert "owner/name" in (result.error or "")


@pytest.mark.asyncio
async def test_code_fetch_search_returns_snapshot_metadata(tmp_path: Path) -> None:
    _seed_snapshot(tmp_path)
    result = await code_fetch("owner/repo", query="authenticate", ctx=None)
    assert result.outcome in {"ok", "partial"}
    assert result.branch == "main"
    assert result.resolved_commit == "a" * 40
    assert isinstance(result.cache_age_seconds, int)
    assert result.intent == "search"
    assert any(
        "authenticate" in hit.snippet
        or (hit.symbol or {}).get("name") == "authenticate"
        for hit in result.hits
    )


@pytest.mark.asyncio
async def test_code_fetch_reads_file_from_snapshot(tmp_path: Path) -> None:
    _seed_snapshot(tmp_path)
    result = await code_fetch("owner/repo", path="src/auth.py", ctx=None)
    assert result.intent == "read"
    assert result.resolved_commit == "a" * 40
    assert "def authenticate" in (result.content or "")


@pytest.mark.asyncio
async def test_code_fetch_lists_tree(tmp_path: Path) -> None:
    _seed_snapshot(tmp_path)
    result = await code_fetch("owner/repo", path="src", ctx=None)
    assert result.intent == "tree"
    assert "src/auth.py" in result.tree


@pytest.mark.asyncio
async def test_code_fetch_lists_tree_with_depth(tmp_path: Path) -> None:
    source = tmp_path / "deep_repo"
    (source / "level1" / "level2").mkdir(parents=True)
    (source / "level1" / "file1.py").write_text("# l1\n", encoding="utf-8")
    (source / "level1" / "level2" / "file2.py").write_text("# l2\n", encoding="utf-8")
    manager = SnapshotManager(
        db_path=str(tmp_path / "deep_snap.sqlite"),
        worktree_root=tmp_path / "deep_worktrees",
    )
    manager.build_from_directory("owner/deep", "main", "b" * 40, source)
    reset_snapshot_manager_for_tests(manager)
    # Depth 1 should include level1/file1.py but exclude level1/level2/file2.py
    result = await code_fetch("owner/deep", path="level1", depth=1, ctx=None)
    assert result.intent == "tree"
    assert "level1/file1.py" in result.tree
    assert "level1/level2/file2.py" not in result.tree

@pytest.mark.asyncio
async def test_code_fetch_graph_symbol(tmp_path: Path) -> None:
    _seed_snapshot(tmp_path)
    result = await code_fetch("owner/repo", symbol="authenticate", ctx=None)
    assert result.intent == "graph"
    assert result.hits


@pytest.mark.asyncio
async def test_code_fetch_regex_search(tmp_path: Path) -> None:
    _seed_snapshot(tmp_path)
    result = await code_fetch("owner/repo", query=r"def authenticate", regexp=True, ctx=None)
    assert result.intent == "search"
    assert result.hits
    assert any("regex" in hit.why for hit in result.hits)
@pytest.mark.asyncio
async def test_code_fetch_reads_windowed_lines(tmp_path: Path) -> None:
    _seed_snapshot(tmp_path)
    result = await code_fetch("owner/repo", path="src/auth.py", start_line=4, end_line=5, ctx=None)
    assert result.outcome == "ok"
    assert result.intent == "read"
    assert "def authenticate" in (result.content or "")
    assert "def login" not in (result.content or "")
    assert result.hits[0].start_line == 4
    assert result.hits[0].end_line == 5


@pytest.mark.asyncio
async def test_code_fetch_rejects_window_with_query(tmp_path: Path) -> None:
    _seed_snapshot(tmp_path)
    result = await code_fetch(
        "owner/repo",
        query="authenticate",
        path="src/auth.py",
        start_line=2,
        end_line=3,
        ctx=None,
    )
    assert result.outcome == "error"
    assert "start_line/end_line" in (result.error or "")


@pytest.mark.asyncio
async def test_code_fetch_rejects_window_without_path(tmp_path: Path) -> None:
    _seed_snapshot(tmp_path)
    result = await code_fetch("owner/repo", start_line=2, end_line=3, ctx=None)
    assert result.outcome == "error"
    assert "start_line/end_line" in (result.error or "")


@pytest.mark.asyncio
async def test_code_fetch_window_read_has_no_truncation_flags(tmp_path: Path) -> None:
    _seed_snapshot(tmp_path)
    result = await code_fetch("owner/repo", path="src/auth.py", start_line=4, end_line=5, ctx=None)
    assert result.outcome == "ok"
    assert result.intent == "read"
    assert result.snapshot_truncated is False
    assert result.search_truncated is False


@pytest.mark.asyncio
async def test_code_fetch_reports_snapshot_truncation(tmp_path: Path) -> None:
    source = tmp_path / "trunc_repo"
    (source / "src").mkdir(parents=True)
    (source / "src" / "a.py").write_text("def alpha():\n    pass\n", encoding="utf-8")
    manager = SnapshotManager(
        db_path=str(tmp_path / "trunc_snap.sqlite"),
        worktree_root=tmp_path / "trunc_worktrees",
    )
    manager.build_from_directory("owner/trunc", "main", "c" * 40, source, truncated=True)
    reset_snapshot_manager_for_tests(manager)
    result = await code_fetch("owner/trunc", query="alpha", ctx=None)
    assert result.outcome in {"ok", "partial"}
    assert result.snapshot_truncated is True
    assert result.truncated is True
    assert result.search_truncated is False


def test_code_search_next_points_to_code_fetch_without_ref() -> None:
    from kindly_web_search_mcp_server.tools.code_search.models import (
        CodeSearchHit,
        CodeSearchResultType,
        QueryMetadata,
        Stats,
        _build_next,
    )

    hit = CodeSearchHit(
        repository="owner/repo",
        path="src/auth.py",
        url="https://github.com/owner/repo/blob/main/src/auth.py",
        commit_oid="c" * 40,
        line_start=4,
        line_end=6,
        provider="github",
    )
    nexts = _build_next(
        CodeSearchResultType(
            query="authenticate",
            results=[hit],
            outcome="ok",
            repositories=[],
            diagnostics=[],
            stats=Stats(),
            query_metadata=QueryMetadata(original_query="authenticate"),
        ),
        plan=type("Plan", (), {"anchor_terms": ["authenticate"], "variants": []})(),
    )
    assert nexts[0].tool == "code_fetch"
    assert nexts[0].query["repository"] == "owner/repo"
    assert nexts[0].query["query"] == "authenticate"
    assert "path" not in nexts[0].query


@pytest.mark.asyncio
async def test_code_fetch_error_message_nonempty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport error with an empty message still yields a non-empty error."""
    import httpx

    from kindly_web_search_mcp_server.tools.code_search import snapshot as snapshot_module

    def _boom(repository: str, *, ref: str | None = None):
        raise snapshot_module.SnapshotError(
            f"GitHub repository lookup failed: {str(httpx.TimeoutException('')) or type(httpx.TimeoutException('')).__name__}"
        )

    monkeypatch.setattr(snapshot_module, "_resolve_main_commit", _boom)
    result = await code_fetch("owner/repo", ctx=None)
    assert result.outcome == "error"
    assert result.error
    assert not (result.error or "").endswith(": ")


@pytest.mark.asyncio
async def test_code_fetch_map_reports_graph_ready(tmp_path: Path) -> None:
    _seed_snapshot(tmp_path)
    result = await code_fetch("owner/repo", ctx=None)
    assert result.intent == "map"
    assert result.graph is not None
    assert result.graph.status == "ready"
    assert result.graph.symbol_count > 0
    assert (result.map or {}).get("files")
    assert "src/auth.py" in (result.map or {})["files"]
    assert "README.md" in (result.map or {})["files"]

@pytest.mark.asyncio
async def test_code_fetch_symbol_waits_for_pending_graph(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    (source / "src").mkdir(parents=True)
    (source / "src" / "auth.py").write_text(
        "def login():\n    return True\n\ndef authenticate():\n    return login()\n",
        encoding="utf-8",
    )
    manager = SnapshotManager(
        db_path=str(tmp_path / "snap.sqlite"),
        worktree_root=tmp_path / "worktrees",
    )
    snapshot = manager.build_from_directory("owner/repo", "main", "a" * 40, source, defer_graph=True)
    snapshot.graph_task = asyncio.create_task(manager._deferred_graph_build(snapshot))
    reset_snapshot_manager_for_tests(manager)
    result = await code_fetch("owner/repo", symbol="authenticate", ctx=None)
    assert result.intent == "graph"
    assert any("graph:definition" in hit.why for hit in result.hits)
    assert result.graph is not None
    assert result.graph.status == "ready"


@pytest.mark.asyncio
async def test_code_fetch_symbol_pending_falls_back_with_status(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    (source / "src").mkdir(parents=True)
    (source / "src" / "auth.py").write_text(
        "def login():\n    return True\n\ndef authenticate():\n    return login()\n",
        encoding="utf-8",
    )
    manager = SnapshotManager(
        db_path=str(tmp_path / "snap.sqlite"),
        worktree_root=tmp_path / "worktrees",
    )
    manager.build_from_directory("owner/repo", "main", "a" * 40, source, defer_graph=True)
    reset_snapshot_manager_for_tests(manager)
    result = await code_fetch("owner/repo", symbol="authenticate", ctx=None)
    assert result.graph is not None
    assert result.graph.status == "pending"
    assert "still building" in (result.warning or "")


@pytest.mark.asyncio
async def test_code_fetch_neighbors_skip_unresolved_edges(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    (source / "src").mkdir(parents=True)
    (source / "src" / "auth.py").write_text(
        "def login():\n    return True\n\ndef authenticate():\n    print(42)\n    return login()\n",
        encoding="utf-8",
    )
    manager = SnapshotManager(
        db_path=str(tmp_path / "snap.sqlite"),
        worktree_root=tmp_path / "worktrees",
    )
    manager.build_from_directory("owner/repo", "main", "a" * 40, source)
    reset_snapshot_manager_for_tests(manager)
    result = await code_fetch("owner/repo", symbol="authenticate", ctx=None)
    assert result.hits
    for hit in result.hits:
        for callee in hit.callees:
            assert callee.path, f"unresolved edge surfaced: {callee.name}"


@pytest.mark.asyncio
async def test_code_fetch_search_next_points_to_file_read(tmp_path: Path) -> None:
    _seed_snapshot(tmp_path)
    result = await code_fetch("owner/repo", query="authenticate", ctx=None)
    assert result.intent == "search"
    assert result.next
    assert result.next[0].path
    hit_paths = {hit.path for hit in result.hits}
    assert result.next[0].path in hit_paths


@pytest.mark.asyncio
async def test_code_fetch_pagination_roundtrip(tmp_path: Path) -> None:
    source = tmp_path / "page_repo"
    lines = [f"hit_{i} marker" for i in range(30)]
    (source / "src").mkdir(parents=True)
    (source / "src" / "hits.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manager = SnapshotManager(
        db_path=str(tmp_path / "page_snap.sqlite"),
        worktree_root=tmp_path / "page_worktrees",
    )
    manager.build_from_directory("owner/page", "main", "a" * 40, source)
    reset_snapshot_manager_for_tests(manager)
    first = await code_fetch("owner/page", query="marker", max_matches=10, ctx=None)
    assert first.has_more is True
    assert first.next_cursor
    assert len(first.hits) == 10
    second = await code_fetch(
        "owner/page", query="marker", max_matches=10, cursor=first.next_cursor, ctx=None
    )
    assert len(second.hits) == 10
    first_lines = {hit.start_line for hit in first.hits}
    second_lines = {hit.start_line for hit in second.hits}
    assert first_lines.isdisjoint(second_lines)
    third = await code_fetch(
        "owner/page", query="marker", max_matches=10, cursor=second.next_cursor, ctx=None
    )
    third_lines = {hit.start_line for hit in third.hits}
    assert third_lines.isdisjoint(first_lines | second_lines)
    assert third.has_more is False


@pytest.mark.asyncio
async def test_code_fetch_cursor_expired(tmp_path: Path) -> None:
    _seed_snapshot(tmp_path)
    import base64
    import json

    bad_cursor = base64.urlsafe_b64encode(
        json.dumps({"v": 1, "offset": 10, "commit": "0" * 40, "query": "authenticate"}).encode()
    ).decode("ascii")
    result = await code_fetch("owner/repo", query="authenticate", cursor=bad_cursor, ctx=None)
    assert result.outcome == "error"
    assert "cursor expired" in (result.error or "")


@pytest.mark.asyncio
async def test_code_fetch_case_sensitive(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    (source / "src").mkdir(parents=True)
    (source / "src" / "auth.py").write_text(
        "Login = 1\nlogin = 2\n",
        encoding="utf-8",
    )
    manager = SnapshotManager(
        db_path=str(tmp_path / "snap.sqlite"),
        worktree_root=tmp_path / "worktrees",
    )
    manager.build_from_directory("owner/repo", "main", "a" * 40, source)
    reset_snapshot_manager_for_tests(manager)
    insensitive = await code_fetch("owner/repo", query="Login", ctx=None)
    insensitive_lines = {hit.start_line for hit in insensitive.hits}
    assert 1 in insensitive_lines
    assert 2 in insensitive_lines
    sensitive = await code_fetch("owner/repo", query="Login", case_sensitive=True, ctx=None)
    sensitive_lines = {hit.start_line for hit in sensitive.hits}
    assert 1 in sensitive_lines
    assert 2 not in sensitive_lines


@pytest.mark.asyncio
async def test_code_fetch_language_filter(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    (source / "src").mkdir(parents=True)
    (source / "src" / "auth.py").write_text("token = 'py'\n", encoding="utf-8")
    (source / "README.md").write_text("token appears here\n", encoding="utf-8")
    manager = SnapshotManager(
        db_path=str(tmp_path / "snap.sqlite"),
        worktree_root=tmp_path / "worktrees",
    )
    manager.build_from_directory("owner/repo", "main", "a" * 40, source)
    reset_snapshot_manager_for_tests(manager)
    result = await code_fetch("owner/repo", query="token", language="python", ctx=None)
    assert result.hits
    assert all(hit.path.endswith(".py") for hit in result.hits)


@pytest.mark.asyncio
async def test_code_fetch_ref_isolation(tmp_path: Path) -> None:
    import sqlite3
    from unittest import mock

    from kindly_web_search_mcp_server.tools.code_search import snapshot as snapshot_module

    source = tmp_path / "repo"
    (source / "src").mkdir(parents=True)
    (source / "src" / "auth.py").write_text(
        "def login():\n    return True\n\ndef authenticate():\n    return login()\n",
        encoding="utf-8",
    )
    manager = SnapshotManager(
        db_path=str(tmp_path / "snap.sqlite"),
        worktree_root=tmp_path / "worktrees",
    )
    manager.build_from_directory("owner/refrepo", "main", "a" * 40, source)
    reset_snapshot_manager_for_tests(manager)

    async def _fake_resolve(repository: str, *, ref: str | None = None):
        return ("v1", "d" * 40)

    async def _fake_download(repository: str, _sha: str) -> Path:
        return source

    with (
        mock.patch.object(snapshot_module, "_resolve_main_commit", _fake_resolve),
        mock.patch.object(snapshot_module, "_download_tarball", _fake_download),
    ):
        snapshot = await manager.refresh("owner/refrepo", ref="v1")
        if snapshot.graph_task is not None:
            await snapshot.graph_task

    con = sqlite3.connect(str(tmp_path / "snap.sqlite"))
    try:
        main_count = con.execute(
            "SELECT COUNT(*) FROM symbols WHERE repository = 'owner/refrepo'"
        ).fetchone()[0]
        ref_count = con.execute(
            "SELECT COUNT(*) FROM symbols WHERE repository = 'owner/refrepo@v1'"
        ).fetchone()[0]
    finally:
        con.close()
    assert main_count > 0
    assert ref_count > 0


@pytest.mark.asyncio
async def test_code_fetch_search_filters_thread_to_hits(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    (source / "src").mkdir(parents=True)
    (source / "src" / "auth.py").write_text("needle = 1\n", encoding="utf-8")
    (source / "docs").mkdir(parents=True)
    (source / "docs" / "notes.md").write_text("needle mentioned\n", encoding="utf-8")
    manager = SnapshotManager(
        db_path=str(tmp_path / "snap.sqlite"),
        worktree_root=tmp_path / "worktrees",
    )
    manager.build_from_directory("owner/repo", "main", "a" * 40, source)
    reset_snapshot_manager_for_tests(manager)
    result = await code_fetch("owner/repo", query="needle", exclude_glob="docs/*", ctx=None)
    assert result.hits
    assert all(not hit.path.startswith("docs/") for hit in result.hits)
