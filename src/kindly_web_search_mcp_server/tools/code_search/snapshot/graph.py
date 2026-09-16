"""Symbol/edge graph extraction using TreeSitter evidence."""

from __future__ import annotations

from typing import Iterable

from ..tree_sitter_evidence import classify_source


def _extract_graph(
    records: Iterable[tuple[str, str | None, int, str]],
) -> tuple[
    list[tuple[str, str, str, int, int]],
    list[tuple[str, str, int | None, str, str, str | None, int | None, float]],
]:
    symbols: list[tuple[str, str, str, int, int]] = []
    calls: list[tuple[str, str, int, str]] = []
    definitions: dict[str, list[tuple[str, int]]] = {}
    for path, language, _size, content in records:
        classification = classify_source(content, language=language, path=path)
        scope_stack: list[tuple[str, int, int]] = []
        for item in classification.evidence:
            name = item.name
            if not name:
                continue
            while scope_stack and scope_stack[-1][2] < item.start_line:
                scope_stack.pop()
            if item.role == "definition":
                symbols.append((path, name, item.kind, item.start_line, item.end_line))
                definitions.setdefault(name, []).append((path, item.start_line))
                scope_stack.append((name, item.start_line, item.end_line))
            elif item.role == "callsite" and scope_stack:
                enclosing = scope_stack[-1][0]
                calls.append((enclosing, path, item.start_line, name))
    edges: list[tuple[str, str, int | None, str, str, str | None, int | None, float]] = []
    for source_name, source_path, source_line, target_name in calls:
        targets = definitions.get(target_name, [])
        if len(targets) == 1:
            target_path, target_line = targets[0]
            edges.append(
                (
                    source_name,
                    source_path,
                    source_line,
                    "calls",
                    target_name,
                    target_path,
                    target_line,
                    0.8,
                )
            )
        elif len(targets) > 1:
            same_file = [t for t in targets if t[0] == source_path]
            if same_file:
                target_path, target_line = same_file[0]
                confidence = 0.7
            else:
                target_path, target_line = targets[0]
                confidence = 0.5
            edges.append(
                (
                    source_name,
                    source_path,
                    source_line,
                    "calls",
                    target_name,
                    target_path,
                    target_line,
                    confidence,
                )
            )
        elif not targets:
            edges.append(
                (source_name, source_path, source_line, "calls", target_name, None, None, 0.3)
            )
    return symbols, edges
