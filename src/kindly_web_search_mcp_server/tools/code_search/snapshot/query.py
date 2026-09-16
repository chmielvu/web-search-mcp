"""Search and graph-query helpers for SnapshotManager.

Module-level functions operate on a :class:`SnapshotManager` instance passed in
as the first argument. The public :meth:`SnapshotManager.query` and
``query_async`` methods call into these helpers without thin wrappers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import SnapshotManager

from .embeddings import _cosine_similarity, _hf_batch_code_embeddings, _hf_code_embedding
from .models import (
    RelatedSymbol,
    Snapshot,
    SnapshotError,
    SnapshotHit,
    _repo_key,
)
from .persist import _connect
from .scan import (
    _first_match_snippet,
    _iter_files,
    _list_tree,
    _matches_filters,
    _merge_hits,
    _read_text,
    _scan_literal,
    _scan_regex,
    _snippet,
)


def _fts_hits(
    manager: SnapshotManager,
    repo_key: str,
    query: str,
    *,
    path_prefix: str | None,
    limit: int,
    language: str | None = None,
    filename: str | None = None,
    path_glob: str | None = None,
    exclude_glob: str | None = None,
    case_sensitive: bool = False,
) -> list[str]:
    # FTS5 MATCH is case-insensitive; when case-sensitive matching is
    # requested the literal scan carries the query instead.
    if case_sensitive:
        return []
    terms = [token for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", query) if len(token) > 1]
    if not terms:
        return []
    terms = terms[:8]

    def _run(match: str) -> list:
        import sqlite3

        base_sql = "SELECT path FROM files_fts WHERE repository = ? AND files_fts MATCH ?"
        args: list[Any] = [repo_key, match]
        if path_prefix:
            base_sql += " AND path LIKE ?"
            args.append(f"{path_prefix}%")
        ranked_sql = f"{base_sql} ORDER BY bm25(files_fts) LIMIT ?"
        ranked_args = [*args, limit]
        with manager._lock:
            con = _connect(manager)
            try:
                try:
                    return con.execute(ranked_sql, ranked_args).fetchall()
                except sqlite3.OperationalError:
                    return con.execute(f"{base_sql} LIMIT ?", [*args, limit]).fetchall()
            except sqlite3.OperationalError:
                return []
            finally:
                con.close()

    rows = _run(" AND ".join(f'"{term}"' for term in terms))
    if not rows and len(terms) > 1:
        rows = _run(" OR ".join(f'"{term}"' for term in terms))
    return [
        path
        for path in (str(row["path"]) for row in rows)
        if _matches_filters(
            path,
            language=language,
            filename=filename,
            path_glob=path_glob,
            exclude_glob=exclude_glob,
        )
    ]


def _neighbors(
    manager: SnapshotManager,
    repo_key: str,
    name: str | None,
    path: str,
) -> tuple[list[RelatedSymbol], list[RelatedSymbol]]:
    if not name:
        return [], []
    with manager._lock:
        con = _connect(manager)
        try:
            callers = con.execute(
                "SELECT source_name, source_path, source_line FROM edges "
                "WHERE repository = ? AND target_name = ? AND target_path IS NOT NULL LIMIT 3",
                (repo_key, name),
            ).fetchall()
            callees = con.execute(
                "SELECT target_name, target_path, target_line FROM edges "
                "WHERE repository = ? AND source_name = ? AND source_path = ? "
                "AND target_path IS NOT NULL LIMIT 3",
                (repo_key, name, path),
            ).fetchall()
        finally:
            con.close()
    return (
        [
            RelatedSymbol(str(row["source_name"]), str(row["source_path"]), row["source_line"])
            for row in callers
        ],
        [
            RelatedSymbol(
                str(row["target_name"]),
                str(row["target_path"] or ""),
                row["target_line"],
            )
            for row in callees
        ],
    )


def _symbol_at(
    manager: SnapshotManager, repo_key: str, path: str, line: int
) -> tuple[str, str | None, str] | None:
    with manager._lock:
        con = _connect(manager)
        try:
            row = con.execute(
                "SELECT name, kind FROM symbols WHERE repository = ? AND path = ? "
                "AND start_line <= ? AND end_line >= ? LIMIT 1",
                (repo_key, path, line, line),
            ).fetchone()
        finally:
            con.close()
    if row is None:
        return None
    return str(row["name"]), row["kind"], "definition"


def _hit_from_file(
    manager: SnapshotManager,
    snapshot: Snapshot,
    path: str,
    query: str,
    *,
    context_lines: int,
    why: list[str],
) -> SnapshotHit:
    file_path = snapshot.root / path
    start_line, end_line, snippet = _first_match_snippet(file_path, query, context_lines)
    return SnapshotHit(
        path=path,
        start_line=start_line,
        end_line=end_line,
        why=why,
        snippet=snippet,
        confidence=0.7,
    )


def _graph_hits(
    manager: SnapshotManager,
    snapshot: Snapshot,
    symbol: str,
    *,
    limit: int,
    context_lines: int,
) -> list[SnapshotHit]:
    with manager._lock:
        con = _connect(manager)
        try:
            defs = con.execute(
                "SELECT path, name, kind, start_line, end_line FROM symbols "
                "WHERE repository = ? AND name = ? LIMIT ?",
                (_repo_key(snapshot), symbol, limit),
            ).fetchall()
        finally:
            con.close()
    hits: list[SnapshotHit] = []
    for row in defs:
        path = str(row["path"])
        start_line = int(row["start_line"])
        end_line = int(row["end_line"])
        callers, callees = _neighbors(manager, _repo_key(snapshot), symbol, path)
        hits.append(
            SnapshotHit(
                path=path,
                start_line=start_line,
                end_line=end_line,
                symbol_name=str(row["name"]),
                symbol_kind=str(row["kind"] or "definition"),
                role="definition",
                why=["graph:definition"],
                snippet=_snippet(snapshot.root / path, start_line, end_line, context_lines),
                callers=callers,
                callees=callees,
                confidence=0.9,
            )
        )
    if hits:
        return hits[:limit]
    return [
        _hit_from_file(
            manager,
            snapshot,
            path,
            symbol,
            context_lines=context_lines,
            why=["graph:name"],
        )
        for path in _fts_hits(manager, _repo_key(snapshot), symbol, path_prefix=None, limit=limit)
    ]


def _architecture(manager: SnapshotManager, snapshot: Snapshot) -> dict[str, Any]:
    with manager._lock:
        con = _connect(manager)
        try:
            languages = con.execute(
                "SELECT language, COUNT(*) AS n FROM files WHERE repository = ? "
                "GROUP BY language ORDER BY n DESC LIMIT 12",
                (_repo_key(snapshot),),
            ).fetchall()
            top_symbols = con.execute(
                "SELECT name, kind, path, start_line FROM symbols WHERE repository = ? "
                "ORDER BY start_line LIMIT 20",
                (_repo_key(snapshot),),
            ).fetchall()
        finally:
            con.close()
    files_list = _list_tree(snapshot.root, "", limit=200, depth=None)
    return {
        "file_count": snapshot.file_count,
        "languages": {str(row["language"] or "unknown"): int(row["n"]) for row in languages},
        "symbols": [
            {
                "name": str(row["name"]),
                "kind": row["kind"],
                "path": str(row["path"]),
                "line": int(row["start_line"]),
            }
            for row in top_symbols
        ],
        "files": files_list,
        "files_truncated": len(files_list) >= 200,
    }


def _search_hits(
    manager: SnapshotManager,
    snapshot: Snapshot,
    query: str,
    *,
    path_prefix: str | None,
    regexp: bool,
    limit: int,
    context_lines: int,
    offset: int = 0,
    language: str | None = None,
    filename: str | None = None,
    path_glob: str | None = None,
    exclude_glob: str | None = None,
    case_sensitive: bool = False,
) -> tuple[list[SnapshotHit], bool, bool]:
    offset = max(0, offset)
    # Over-fetch by one so a full page can prove has_more without a re-query.
    fetch_limit = offset + limit + 1
    filters = {
        "language": language,
        "filename": filename,
        "path_glob": path_glob,
        "exclude_glob": exclude_glob,
    }
    if regexp:
        try:
            pattern = re.compile(query)
        except re.error as exc:
            raise SnapshotError(f"invalid regular expression: {exc}") from exc
        scanned, scan_truncated = _scan_regex(
            snapshot.root,
            pattern,
            path_prefix=path_prefix,
            limit=fetch_limit,
            context_lines=context_lines,
            case_sensitive=case_sensitive,
            **filters,
        )
        page = scanned[offset : offset + limit]
        has_more = (len(scanned) > offset + limit or scan_truncated) and bool(page)
        return page, scan_truncated, has_more
    hits: list[SnapshotHit] = []
    fts_hits = _fts_hits(
        manager,
        _repo_key(snapshot),
        query,
        path_prefix=path_prefix,
        limit=fetch_limit,
        case_sensitive=case_sensitive,
        **filters,
    )
    if fts_hits:
        hits = [
            _hit_from_file(manager, snapshot, path, query, context_lines=context_lines, why=["fts"])
            for path in fts_hits
        ]
    # FTS uses an AND expression first. If no file contains every term,
    # search each term so natural-language queries still return useful
    # repository-wide candidates instead of a false empty result.
    if not fts_hits and " " in query.strip():
        terms = [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", query) if len(t) > 1]
        if len(terms) > 1:
            scan_truncated = False
            for term in terms:
                scanned, term_truncated = _scan_literal(
                    snapshot.root,
                    term,
                    path_prefix=path_prefix,
                    limit=fetch_limit,
                    context_lines=context_lines,
                    case_sensitive=case_sensitive,
                    **filters,
                )
                hits = _merge_hits(hits, scanned, fetch_limit)
                scan_truncated = scan_truncated or term_truncated
                if len(hits) >= fetch_limit:
                    break
            for hit in hits:
                hit.callers, hit.callees = _neighbors(
                    manager, _repo_key(snapshot), hit.symbol_name or query, hit.path
                )
                if hit.symbol_name is None:
                    symbol = _symbol_at(manager, _repo_key(snapshot), hit.path, hit.start_line)
                    if symbol is not None:
                        hit.symbol_name, hit.symbol_kind, hit.role = symbol
            page = hits[offset : offset + limit]
            has_more = (len(hits) > offset + limit or scan_truncated) and bool(page)
            return page, scan_truncated or len(hits) >= fetch_limit, has_more
    scanned, scan_truncated = _scan_literal(
        snapshot.root,
        query,
        path_prefix=path_prefix,
        limit=fetch_limit,
        context_lines=context_lines,
        case_sensitive=case_sensitive,
        **filters,
    )

    hits = _merge_hits(hits, scanned, fetch_limit)
    for hit in hits:
        hit.callers, hit.callees = _neighbors(
            manager, _repo_key(snapshot), hit.symbol_name or query, hit.path
        )
        if hit.symbol_name is None:
            symbol = _symbol_at(manager, _repo_key(snapshot), hit.path, hit.start_line)
            if symbol is not None:
                hit.symbol_name, hit.symbol_kind, hit.role = symbol
    page = hits[offset : offset + limit]
    has_more = (len(hits) > offset + limit or scan_truncated) and bool(page)
    return page, scan_truncated or len(hits) >= fetch_limit, has_more


async def _semantic_search_hits(
    manager: SnapshotManager,
    snapshot: Snapshot,
    query: str,
    *,
    path_prefix: str | None,
    limit: int,
    context_lines: int,
) -> list[SnapshotHit]:
    """Semantic fallback via the shared ml/ client (Arctic, 384-dim).

    Only invoked when FTS+literal yield 0 hits. Batches file snippets
    (path + first 1k chars) through the fastembed-snowflake service,
    compares via cosine to the query embedding, returns top hits with
    why=["semantic"] and confidence = cosine similarity.
    """
    if not query or len(query.strip()) < 3:
        return []
    query_emb = await _hf_code_embedding(query, max_chars=2000)
    if query_emb is None:
        return []
    # Gather candidate files — cap to avoid huge embed batches
    try:
        candidates = list(_iter_files(snapshot.root, path_prefix))
    except Exception:
        return []
    if not candidates:
        return []
    # Repo-wide semantic is expensive — cap at 300 files (balanced vs cost)
    # Path-scoped searches keep all candidates (cheap, already filtered)
    if path_prefix is None and len(candidates) > 300:
        # Prioritize smaller files / recently modified could help, but simple slice is deterministic
        candidates = candidates[:300]
    candidate_texts: list[str] = []
    candidate_paths: list[Path] = []
    for p in candidates:
        try:
            txt = _read_text(p)
            rel = p.relative_to(snapshot.root).as_posix()
            snippet_for_emb = f"{rel}\n{txt[:1200]}"
            candidate_texts.append(snippet_for_emb[:2000])
            candidate_paths.append(p)
        except Exception:
            continue
    if not candidate_texts:
        return []
    embeddings = await _hf_batch_code_embeddings(candidate_texts, max_chars=2000)
    scored: list[tuple[float, Path]] = []
    for path, emb in zip(candidate_paths, embeddings, strict=False):
        if emb is None:
            continue
        sim = _cosine_similarity(query_emb, emb)
        # Code RAG best practice: Top-K 3-5, calibrated threshold, not universal.
        # Web search shows typical RAG thresholds 0.6-0.75 for high precision (vs 0.15 permissive
        # would return noise). Use 0.60 for code (L2-normalized embeddings) with adaptive fallback:
        # if we have <3 candidates above 0.60, relax to 0.50, but never below 0.45 for code.
        # This avoids nonsense queries returning noise while keeping real code queries.
        if sim > 0.60:
            scored.append((sim, path))
    # Adaptive relaxation: if strict 0.60 yields <3 hits but we have high-ish scores, relax to 0.50
    if len(scored) < 3:
        for path, emb in zip(candidate_paths, embeddings, strict=False):
            if emb is None:
                continue
            sim = _cosine_similarity(query_emb, emb)
            # avoid duplicates already in scored
            if 0.50 < sim <= 0.60 and not any(p == path for _, p in scored):
                scored.append((sim, path))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    hits: list[SnapshotHit] = []
    for sim, path in scored[:limit]:
        rel = path.relative_to(snapshot.root).as_posix()
        # Build snippet around best understanding: use _first_match_snippet fallback
        try:
            start, end, snippet = _first_match_snippet(path, query, context_lines)
        except Exception:
            snippet = ""
            start, end = 1, 1
        hits.append(
            SnapshotHit(
                path=rel,
                start_line=start,
                end_line=end,
                why=["semantic"],
                snippet=snippet,
                confidence=float(sim),
            )
        )
    # Enrich with neighbors/symbol like _search_hits does
    for hit in hits:
        try:
            hit.callers, hit.callees = _neighbors(
                manager, _repo_key(snapshot), hit.symbol_name or query, hit.path
            )
            if hit.symbol_name is None:
                sym = _symbol_at(manager, _repo_key(snapshot), hit.path, hit.start_line)
                if sym is not None:
                    hit.symbol_name, hit.symbol_kind, hit.role = sym
        except Exception:
            continue
    return hits


async def _search_hits_async(
    manager: SnapshotManager,
    snapshot: Snapshot,
    query: str,
    *,
    path_prefix: str | None,
    regexp: bool,
    limit: int,
    context_lines: int,
) -> tuple[list[SnapshotHit], bool]:
    """Async search with semantic fallback when FTS+literal miss.

    Preserves sync _search_hits semantics for all existing callers; semantic
    hits are merged via RRF-like _merge_hits (dedup by path+line). If FTS
    yielded hits, semantic is not invoked (to avoid cost) — fallback only
    when full sync search returns 0 hits. Caller can extend to RRF fusion
    of FTS and semantic via _merge_hits ordering (currently semantic after).
    """
    if regexp:
        # Semantic fallback never for regex
        hits, _truncated, _has_more = _search_hits(
            manager,
            snapshot,
            query,
            path_prefix=path_prefix,
            regexp=True,
            limit=limit,
            context_lines=context_lines,
        )
        return hits, _truncated
    hits, truncated, _has_more = _search_hits(
        manager,
        snapshot,
        query,
        path_prefix=path_prefix,
        regexp=False,
        limit=limit,
        context_lines=context_lines,
    )
    if hits or truncated:
        return hits, truncated
    # No hits — try semantic via the shared ml/ embedding client
    sem_hits = await _semantic_search_hits(
        manager, snapshot, query, path_prefix=path_prefix, limit=limit, context_lines=context_lines
    )
    if sem_hits:
        # RRF-like merge: keep semantic hits as result; if we later have FTS hits,
        # merge would be _merge_hits(hits, sem_hits, limit)
        return sem_hits[:limit], False
    return hits, truncated
