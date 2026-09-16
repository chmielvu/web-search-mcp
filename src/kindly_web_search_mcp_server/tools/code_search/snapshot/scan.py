"""Searching helpers: ripgrep and Python literal/regex scanning, snippet extraction."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from fnmatch import fnmatch
from pathlib import Path

from ..tree_sitter_evidence import language_for_path
from .models import MAX_SNIPPET_CHARS, SnapshotHit, _SKIP_DIRS, _SKIP_SUFFIXES


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def _iter_files(root: Path, path_prefix: str | None) -> "list[Path] | object":
    base = root / path_prefix if path_prefix else root
    if base.is_file():
        yield base
        return
    if not base.exists():
        return
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.casefold() in _SKIP_SUFFIXES:
            continue
        yield path


def _matches_filters(
    rel_path: str,
    *,
    language: str | None,
    filename: str | None,
    path_glob: str | None,
    exclude_glob: str | None,
) -> bool:
    """Empty filters pass everything; each set filter must hold."""
    if language:
        inferred = language_for_path(rel_path) or ""
        if inferred.casefold() != language.strip().casefold():
            return False
    if filename and not fnmatch(Path(rel_path).name, filename):
        return False
    if path_glob and not fnmatch(rel_path, path_glob):
        return False
    if exclude_glob and fnmatch(rel_path, exclude_glob):
        return False
    return True


def _list_tree(root: Path, prefix: str, *, limit: int, depth: int | None = None) -> list[str]:
    base = root / prefix if prefix else root
    if not base.exists():
        return []
    entries: list[str] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if depth is not None:
            rel_from_base = path.relative_to(base)
            if len(rel_from_base.parts) > depth:
                continue
        entries.append(relative.as_posix())
        if len(entries) >= limit:
            break
    return entries


def _first_match_snippet(path: Path, query: str, context_lines: int) -> tuple[int, int, str]:
    if not path.is_file():
        return 1, 1, ""
    lines = _read_text(path).splitlines()
    needle = query.casefold()
    for index, line in enumerate(lines, start=1):
        if needle in line.casefold():
            start = max(1, index - context_lines)
            end = min(len(lines), index + context_lines)
            snippet = "\n".join(lines[start - 1 : end])
            if MAX_SNIPPET_CHARS > 0:
                snippet = snippet[:MAX_SNIPPET_CHARS]
            return index, index, snippet
    tokens = [t.casefold() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", query) if len(t) > 1]
    if tokens:
        for index, line in enumerate(lines, start=1):
            line_cf = line.casefold()
            if any(token in line_cf for token in tokens):
                start = max(1, index - context_lines)
                end = min(len(lines), index + context_lines)
                snippet = "\n".join(lines[start - 1 : end])
                if MAX_SNIPPET_CHARS > 0:
                    snippet = snippet[:MAX_SNIPPET_CHARS]
                return index, index, snippet
    snippet = "\n".join(lines[: max(1, context_lines * 2 + 1)])
    if MAX_SNIPPET_CHARS > 0:
        snippet = snippet[:MAX_SNIPPET_CHARS]
    return 1, min(len(lines) or 1, context_lines * 2 + 1), snippet


def _snippet(path: Path, start_line: int, end_line: int, context_lines: int) -> str:
    if not path.is_file():
        return ""
    lines = _read_text(path).splitlines()
    start = max(1, start_line - context_lines)
    end = min(len(lines), end_line + context_lines)
    snippet = "\n".join(lines[start - 1 : end])
    if MAX_SNIPPET_CHARS > 0:
        snippet = snippet[:MAX_SNIPPET_CHARS]
    return snippet


def _merge_hits(left: list[SnapshotHit], right: list[SnapshotHit], limit: int) -> list[SnapshotHit]:
    seen: set[tuple[str, int]] = set()
    merged: list[SnapshotHit] = []
    for hit in [*left, *right]:
        key = (hit.path, hit.start_line)
        if key in seen:
            continue
        seen.add(key)
        merged.append(hit)
        if len(merged) >= limit:
            break
    return merged


def _try_ripgrep_scan(
    root: Path,
    query: str,
    *,
    path_prefix: str | None,
    limit: int,
    context_lines: int,
    is_regex: bool,
    case_sensitive: bool = False,
    language: str | None = None,
    filename: str | None = None,
    path_glob: str | None = None,
    exclude_glob: str | None = None,
) -> tuple[list[SnapshotHit], bool] | None:
    """Try ripgrep (rg) for literal/regex scan - 10-50x faster than Python loop."""
    rg_path = os.environ.get("CODE_FETCH_RG_PATH")
    if rg_path and not Path(rg_path).exists():
        rg_path = None
    if rg_path is None:
        rg_path = shutil.which("rg")
    if rg_path is None:
        return None
    try:
        search_root = root / path_prefix if path_prefix else root
        if not search_root.exists():
            return [], False
        # Use rg --json for structured output, handle literal vs regex
        cmd = [
            rg_path,
            "--json",
            "--no-config",
            "--hidden",
            "--glob",
            "!.git/*",
            "--max-count",
            str(limit),
            "--context",
            str(context_lines),
        ]
        if not is_regex:
            cmd.append("--fixed-strings")
        if not case_sensitive:
            cmd.append("--ignore-case")
        cmd.extend(["--", query, str(search_root)])
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=8, encoding="utf-8", errors="replace"
        )
        if result.returncode not in (0, 1):
            return None
        hits: list[SnapshotHit] = []
        # Parse rg --json: each line is JSON with type "match" or "context"
        # We only care about "match" lines; context is handled via snippet window
        for line in result.stdout.splitlines():
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") != "match":
                continue
            data = obj.get("data", {})
            path_info = data.get("path", {})
            abs_path = path_info.get("text", "")
            line_num = data.get("line_number", 1)
            try:
                file_path = Path(abs_path)
                relative = file_path.relative_to(root).as_posix()
            except Exception:
                continue
            if not _matches_filters(
                relative,
                language=language,
                filename=filename,
                path_glob=path_glob,
                exclude_glob=exclude_glob,
            ):
                continue
            # For simplicity, re-read file and build snippet window as before
            try:
                lines = _read_text(file_path).splitlines()
                start = max(1, line_num - context_lines)
                end = min(len(lines), line_num + context_lines)
                snippet_text = "\n".join(lines[start - 1 : end])
                if MAX_SNIPPET_CHARS > 0:
                    snippet_text = snippet_text[:MAX_SNIPPET_CHARS]
                hits.append(
                    SnapshotHit(
                        path=relative,
                        start_line=line_num,
                        end_line=line_num,
                        why=["literal" if not is_regex else "regex"],
                        snippet=snippet_text,
                        confidence=0.85,
                    )
                )
                if len(hits) >= limit:
                    return hits, True
            except Exception:
                continue
        return hits, False
    except Exception:
        return None


def _scan_literal(
    root: Path,
    query: str,
    *,
    path_prefix: str | None,
    limit: int,
    context_lines: int,
    case_sensitive: bool = False,
    language: str | None = None,
    filename: str | None = None,
    path_glob: str | None = None,
    exclude_glob: str | None = None,
) -> tuple[list[SnapshotHit], bool]:
    # Try ripgrep first - 10-50x faster
    rg_result = _try_ripgrep_scan(
        root,
        query,
        path_prefix=path_prefix,
        limit=limit,
        context_lines=context_lines,
        is_regex=False,
        case_sensitive=case_sensitive,
        language=language,
        filename=filename,
        path_glob=path_glob,
        exclude_glob=exclude_glob,
    )
    if rg_result is not None:
        return rg_result
    needle = query if case_sensitive else query.casefold()
    hits: list[SnapshotHit] = []
    scanned_files = 0
    for path in _iter_files(root, path_prefix):
        scanned_files += 1
        if path_prefix is None and scanned_files > 2000:
            return hits, True
        relative = path.relative_to(root).as_posix()
        if not _matches_filters(
            relative,
            language=language,
            filename=filename,
            path_glob=path_glob,
            exclude_glob=exclude_glob,
        ):
            continue
        lines = _read_text(path).splitlines()
        for index, line in enumerate(lines, start=1):
            if case_sensitive:
                if needle not in line:
                    continue
            elif needle not in line.casefold():
                continue
            start = max(1, index - context_lines)
            end = min(len(lines), index + context_lines)
            snippet_text = "\n".join(lines[start - 1 : end])
            if MAX_SNIPPET_CHARS > 0:
                snippet_text = snippet_text[:MAX_SNIPPET_CHARS]
            hits.append(
                SnapshotHit(
                    path=relative,
                    start_line=index,
                    end_line=index,
                    why=["literal"],
                    snippet=snippet_text,
                    confidence=0.85,
                )
            )
            if len(hits) >= limit:
                return hits, True
    return hits, False


def _scan_regex(
    root: Path,
    pattern: re.Pattern[str],
    *,
    path_prefix: str | None,
    limit: int,
    context_lines: int,
    case_sensitive: bool = False,
    language: str | None = None,
    filename: str | None = None,
    path_glob: str | None = None,
    exclude_glob: str | None = None,
) -> tuple[list[SnapshotHit], bool]:
    # Try ripgrep first
    rg_result = _try_ripgrep_scan(
        root,
        pattern.pattern,
        path_prefix=path_prefix,
        limit=limit,
        context_lines=context_lines,
        is_regex=True,
        case_sensitive=case_sensitive,
        language=language,
        filename=filename,
        path_glob=path_glob,
        exclude_glob=exclude_glob,
    )
    if rg_result is not None:
        return rg_result
    hits: list[SnapshotHit] = []
    scanned_files = 0
    for path in _iter_files(root, path_prefix):
        scanned_files += 1
        if path_prefix is None and scanned_files > 2000:
            return hits, True
        relative = path.relative_to(root).as_posix()
        if not _matches_filters(
            relative,
            language=language,
            filename=filename,
            path_glob=path_glob,
            exclude_glob=exclude_glob,
        ):
            continue
        lines = _read_text(path).splitlines()
        for index, line in enumerate(lines, start=1):
            if not pattern.search(line):
                continue
            start = max(1, index - context_lines)
            end = min(len(lines), index + context_lines)
            snippet_text = "\n".join(lines[start - 1 : end])
            if MAX_SNIPPET_CHARS > 0:
                snippet_text = snippet_text[:MAX_SNIPPET_CHARS]
            hits.append(
                SnapshotHit(
                    path=relative,
                    start_line=index,
                    end_line=index,
                    why=["regex"],
                    snippet=snippet_text,
                    confidence=0.8,
                )
            )
            if len(hits) >= limit:
                return hits, True
    return hits, False
