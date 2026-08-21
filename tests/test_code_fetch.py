from __future__ import annotations

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
async def test_code_fetch_multi_term_fts_snippet(tmp_path: Path) -> None:
    _seed_snapshot(tmp_path)
    result = await code_fetch("owner/repo", query="authenticate login", ctx=None)
    assert result.outcome == "ok"
    assert result.hits
    # Should identify the file and match on either authenticate or login rather than falling back to line 1
    assert any(hit.path == "src/auth.py" for hit in result.hits)


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
    assert nexts[0].query["path"] == "src/auth.py"
    assert "ref" not in nexts[0].query
