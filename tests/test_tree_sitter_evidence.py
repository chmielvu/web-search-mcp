from __future__ import annotations

import pytest

from kindly_web_search_mcp_server.tools.code_search import tree_sitter_evidence as ast
from kindly_web_search_mcp_server.tools.code_search.models import CodeSearchHit
from kindly_web_search_mcp_server.tools.code_search.ranking import _evidence_role


_SAMPLES = {
    "python": "from fastapi import FastAPI\nclass App:\n    def run(self):\n        return helper()\n",
    "javascript": "import x from 'x';\nclass App { method() { return helper(); } }\n",
    "typescript": "import { X } from 'x';\ninterface Foo { x: string }\n",
    "go": 'import "fmt"\nfunc main() { fmt.Println(1) }\n',
    "rust": "use std::io;\nfn main() { helper(); }\n",
    "bash": "source lib.sh\nfunction run() { echo hi; }\nrun\n",
    "java": "import java.util.List;\nclass App { void run() { helper(); } }\n",
    "html": '<html><script src="app.js"></script><div>Hi</div></html>',
    "sql": "CREATE TABLE users(id INT); SELECT count(*) FROM users;",
}


def test_required_language_set_matches_approved_scope() -> None:
    assert ast.required_languages() == (
        "bash",
        "go",
        "html",
        "java",
        "javascript",
        "python",
        "rust",
        "sql",
        "typescript",
    )


def test_path_language_aliases() -> None:
    assert ast.language_for_path("src/app.py") == "python"
    assert ast.language_for_path("src/app.tsx") == "typescript"
    assert ast.language_for_path("scripts/run.sh") == "bash"
    assert ast.language_for_path("templates/index.html") == "html"
    assert ast.language_for_path("queries/schema.sql") == "sql"
    assert ast.canonical_language("TS") == "typescript"
    assert ast.canonical_language("shell") == "bash"


def test_missing_cached_grammar_fails_open_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ast, "_cached_languages", lambda: set())
    result = ast.classify_source("def run(): pass", language="python")
    assert result.status == "grammar_not_cached"
    assert result.evidence == ()


@pytest.mark.parametrize("language", sorted(_SAMPLES))
def test_cached_parser_classifies_approved_language(language: str) -> None:
    cached = ast._cached_languages()
    if not cached or language not in cached:
        pytest.skip(f"Tree-sitter grammar is not prefetched: {language}")
    result = ast.classify_source(_SAMPLES[language], language=language, match_line_start=1)
    assert result.status == "ok"
    assert result.language == language
    assert result.evidence
    assert all(item.start_line >= 1 for item in result.evidence)


def test_match_line_filter_and_utf8_coordinates() -> None:
    cached = ast._cached_languages()
    if not cached or "python" not in cached:
        pytest.skip("Tree-sitter Python grammar is not prefetched")
    source = "# café\nclass App:\n    def run(self):\n        return helper()\n"
    result = ast.classify_source(
        source,
        language="python",
        match_line_start=4,
        match_line_end=4,
    )
    assert result.status == "ok"
    assert "callsite" in {item.role for item in result.evidence}
    assert any(item.kind == "call" for item in result.evidence)
    assert all(item.start_line <= 4 <= item.end_line for item in result.evidence)
    metadata = result.as_metadata()
    assert metadata["query_version"] == "code-search-ast-v1"
    assert isinstance(metadata["source_digest"], str)


def test_ranking_prefers_parser_role_over_text_heuristic() -> None:
    hit = CodeSearchHit(
        url="https://github.com/acme/repo/blob/main/app.py",
        path="app.py",
        provider="github",
        query_variant="helper",
        snippet="helper()",
        source_metadata={
            "ast_classification": {
                "status": "ok",
                "evidence": [{"role": "definition", "kind": "function_definition"}],
            }
        },
    )
    assert _evidence_role(hit, "helper()") == ("definition", 0.16)
