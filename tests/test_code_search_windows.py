"""Unit tests for the standalone source window engine."""

from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.tools.code_search.models import CodeSearchHit
from kindly_web_search_mcp_server.tools.code_search.query import build_query_plan
from kindly_web_search_mcp_server.tools.code_search.windows import (
    CandidateWindow,
    FileSource,
    LineRange,
    _expand_window,
    _kind,
    _line_has_term,
    collapse_candidates,
    extract_source_windows,
)


def test_line_range_validation():
    range_valid = LineRange(1, 10)
    assert range_valid.start == 1
    assert range_valid.end == 10
    assert range_valid.width == 10

    with pytest.raises(ValueError, match="invalid line range"):
        LineRange(0, 5)

    with pytest.raises(ValueError, match="invalid line range"):
        LineRange(10, 5)


def test_exact_identifier_boundary_matching():
    line1 = "def retry_with_backoff_async(attempts: int):"
    line2 = "def retry_with_backoff(attempts: int):"
    line3 = "    return retry_with_backoff()"

    # Whole identifier matching
    assert _line_has_term(line2, "retry_with_backoff")
    assert _line_has_term(line3, "retry_with_backoff")
    # Should NOT falsely match the compound suffix identifier
    assert not _line_has_term(line1, "retry_with_backoff_asyn")

    # Sub-component matching (splits on _ or -)
    assert _line_has_term(line2, "retry")
    assert _line_has_term(line2, "backoff")


def test_declaration_expansion_python():
    py_code = "\n".join(
        [
            "# Module header",
            "import os",
            "",
            "class WorkerPool:",
            "    def __init__(self, size: int):",
            "        self.size = size",
            "",
            "    def run_job(self, job_id: str):",
            "        print(f'Starting job {job_id}')",
            "        # process target",
            "        result = execute_job(job_id)",
            "        return result",
            "",
            "def helper():",
            "    pass",
        ]
    )
    source = FileSource(repository="org/repo", path="src/pool.py", text=py_code)
    # Match line 11 (result = execute_job(job_id))
    match_lines = [11]
    window = _expand_window(source, match_lines, context=8, max_window_lines=100)

    assert window.start >= 1
    assert window.end <= len(source.lines)
    assert window.width <= 100
    # Must capture enclosing declaration def run_job (line 8)
    assert window.start <= 8
    assert window.end >= 12


def test_declaration_expansion_brace_language():
    ts_code = "\n".join(
        [
            "export class DataPipeline {",
            "  private client: Client;",
            "",
            "  public async executePipeline(task: Task): Promise<Result> {",
            "    const items = await task.loadItems();",
            "    const processed = items.map(transformItem);",
            "    return processed;",
            "  }",
            "}",
        ]
    )
    source = FileSource(repository="org/repo", path="src/pipeline.ts", text=ts_code)
    # Match line 6 (const processed = ...)
    match_lines = [6]
    window = _expand_window(source, match_lines, context=8, max_window_lines=100)

    assert window.start >= 1
    assert window.end <= len(source.lines)
    assert window.start <= 4  # Enclosing method declaration
    assert window.end >= 8  # Method closing brace


def test_overlapping_window_collapse_merges_close_intervals():
    code = "\n".join(f"line_{i} = {i}" for i in range(1, 50))
    source = FileSource(repository="org/repo", path="src/math.py", text=code)

    cand1 = CandidateWindow(
        source=source,
        window=LineRange(10, 20),
        match_lines=(12, 14),
        retrieval_score=2.0,
    )
    cand2 = CandidateWindow(
        source=source,
        window=LineRange(15, 25),
        match_lines=(16, 18),
        retrieval_score=3.0,
    )

    collapsed = collapse_candidates([cand1, cand2])
    assert len(collapsed) == 1
    merged = collapsed[0]
    assert merged.window.start == 10
    assert merged.window.end == 25
    assert set(merged.match_lines) == {12, 14, 16, 18}
    assert merged.retrieval_score == 3.0


def test_overlapping_window_collapse_preserves_disjoint():
    code = "\n".join(f"line_{i} = {i}" for i in range(1, 100))
    source = FileSource(repository="org/repo", path="src/math.py", text=code)

    cand1 = CandidateWindow(
        source=source,
        window=LineRange(5, 15),
        match_lines=(10,),
        retrieval_score=2.0,
    )
    cand2 = CandidateWindow(
        source=source,
        window=LineRange(60, 75),
        match_lines=(65,),
        retrieval_score=2.5,
    )

    collapsed = collapse_candidates([cand1, cand2])
    assert len(collapsed) == 2
    # Sorted by retrieval_score descending
    assert collapsed[0].window.start == 60
    assert collapsed[1].window.start == 5


def test_kind_classification_and_primary_preference():
    code_text = "def solve_equations(a, b):\n    return a + b\n"
    doc_text = "# Solve Equations Guide\nTo solve equations, use solve_equations.\n"

    code_source = FileSource(repository="org/repo", path="src/solver.py", text=code_text)
    doc_source = FileSource(repository="org/repo", path="docs/guide.md", text=doc_text)

    assert _kind(code_source, LineRange(1, 2), [1]) in ("declaration", "code")
    assert _kind(doc_source, LineRange(1, 2), [1]) == "documentation"

    file_sources = {
        code_source.key: code_source,
        doc_source.key: doc_source,
    }
    candidate_hits = [
        CodeSearchHit(
            repository="org/repo",
            path="src/solver.py",
            provider="github",
            url="https://github.com/org/repo/blob/main/src/solver.py",
        ),
        CodeSearchHit(
            repository="org/repo",
            path="docs/guide.md",
            provider="github",
            url="https://github.com/org/repo/blob/main/docs/guide.md",
        ),
    ]

    plan = build_query_plan("solve_equations")
    results = extract_source_windows(plan, file_sources, candidate_hits, max_results=10)

    # Primary preference rule: code window presence drops doc windows
    assert len(results) >= 1
    assert all(res.evidence_role not in ("documentation", "test", "comment") for res in results)
    assert results[0].path == "src/solver.py"
    assert "solve_equations" in (results[0].source_window or "")


def test_regex_matching_on_windows():
    text = "\n".join(
        [
            "def compute_alpha():",
            "    return 1",
            *(f"# spacer line {i}" for i in range(25)),
            "def compute_beta():",
            "    return 2",
        ]
    )
    source = FileSource(repository="org/repo", path="src/compute.py", text=text)
    file_sources = {source.key: source}
    candidate_hits = [
        CodeSearchHit(
            repository="org/repo",
            path="src/compute.py",
            provider="github",
            url="https://github.com/org/repo/blob/main/src/compute.py",
        )
    ]

    plan = build_query_plan("compute", regexp=True)
    plan = build_query_plan("/compute_beta/ regexp")

    results = extract_source_windows(plan, file_sources, candidate_hits, max_results=5)
    assert len(results) == 1
    assert "compute_beta" in (results[0].source_window or "")
    assert "compute_alpha" not in (results[0].source_window or "")
