<!-- generated-by: gsd-doc-writer -->
# Contributing to Kindly Web Search MCP Server

Thank you for your interest in contributing! We welcome contributions from the community.

## Development Setup

1. **Prerequisites**: Python 3.13 or later
2. **Clone the repository**:
   ```bash
   git clone https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server.git
   cd kindly-web-search-mcp-server
   ```
3. **Install development dependencies**:
   ```bash
   pip install -e ".[dev]"
   ```
4. **Set up environment variables** (required for testing):
   - At least one search provider: `SEARXNG_BASE_URL`, `TAVILY_API_KEY`, `BRAVE_API_KEY`, or `JINA_API_KEY`
   - Recommended: `GITHUB_TOKEN` for better GitHub Issue extraction

## Code Style

We use **Ruff** for linting and formatting:

```bash
# Check code style
ruff check src/

# Format code
ruff format src/
```

Please run both commands before submitting a pull request to ensure your code follows project conventions.

## Testing

We use **pytest** for testing. Test files are located in the `tests/` directory following the `test_*.py` naming convention.

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_server.py -v

# Run focused test slice (core search contract)
python -m pytest tests/test_server.py tests/test_page_content_resolver.py tests/test_tool_descriptions.py tests/test_search_router.py tests/test_query_rewrite.py tests/test_search_orchestrator.py
```

All tests should pass before submitting a pull request.

## Pull Request Process

1. **Fork the repository** and create a branch from `main`
2. **Make your changes** in a focused, well-named branch (e.g., `fix/searxng-timeout` or `feature/new-provider`)
3. **Write or update tests** for your changes
4. **Run linting and tests** locally to ensure everything passes
5. **Submit a pull request** with:
   - A clear title describing the change
   - A description of what changed and why
   - Any relevant issue references

PRs are reviewed by maintainers. We aim to review within a few days.

## Commit Message Guidelines

Use clear, descriptive commit messages. A good format:

```
type: brief description

 Longer explanation if needed.
```

Common types: `fix`, `feat`, `refactor`, `docs`, `test`, `chore`

Example:
```
feat: add Brave search provider integration

 Implement Brave Search API as a concurrent Tier 2 provider
 alongside Tavily and Jina. Results merged via RRF.
```

## Reporting Bugs

Report bugs via GitHub Issues: https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server/issues

Please include:
- A clear description of the bug
- Steps to reproduce
- Expected behavior vs actual behavior
- Your environment (OS, Python version, MCP client)
- Any relevant logs or error messages

## Feature Requests

Submit feature requests via GitHub Issues. Describe:
- The feature you want
- Why it would be useful
- Any implementation ideas you have

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

By contributing, you agree that your contributions will be licensed under the same MIT License.