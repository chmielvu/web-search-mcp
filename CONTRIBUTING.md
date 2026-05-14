<!-- generated-by: gsd-doc-writer -->
# Contributing to Kindly Web Search MCP Server

Thank you for your interest in contributing! We welcome contributions from the community.

## Development Setup

See GETTING-STARTED.md for prerequisites and first-run instructions, and DEVELOPMENT.md for local development setup including environment variables and project structure.

**Quick setup:**

1. Clone the repository:
   ```bash
   git clone https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server.git
   cd kindly-web-search-mcp-server
   ```

2. Create a virtual environment and install dev dependencies:
   ```bash
   uv venv
   source .venv/bin/activate  # Linux/macOS
   # or: .venv\Scripts\activate  # Windows PowerShell
   
   uv pip install -e ".[dev]"
   ```

3. Set required environment variables (at least one search provider):
   ```bash
   export SEARXNG_BASE_URL="http://localhost:8080"
   # Or: TAVILY_API_KEY, BRAVE_API_KEY, JINA_API_KEY
   export GITHUB_TOKEN="ghp_..."  # Recommended
   ```

## Coding Standards

We use **Ruff** for linting and formatting. Run these before committing:

```bash
# Check linting issues
ruff check src/

# Auto-fix linting issues
ruff check src/ --fix

# Format code
ruff format src/
```

**Key conventions:**

- Use absolute imports from `src/kindly_web_search_mcp_server.*` namespace
- Write comments for *why*, not *what* — explain intent, not implementation
- Async-first design: use `httpx.AsyncClient` for all I/O operations
- All tool responses use Pydantic models from `models.py`

## Testing Requirements

- **Run tests before committing:** `pytest`
- **New features need tests:** Add unit tests in `tests/` following the `test_*.py` naming convention
- **Bug fixes need regression tests:** Add a test that would have caught the bug

**Running tests:**

```bash
# All tests
pytest

# Focused test slice (core search contract)
python -m pytest tests/test_server.py tests/test_page_content_resolver.py tests/test_tool_descriptions.py tests/test_search_router.py tests/test_query_rewrite.py tests/test_search_orchestrator.py

# Single test file
python -m pytest tests/test_searxng_unit.py -v
```

**Mocking pattern:** Patch under `kindly_web_search_mcp_server.*` namespace:

```python
with patch("kindly_web_search_mcp_server.content.resolver.parse_stackexchange_url", ...):
    # test code
```

## Pull Request Process

1. **Fork the repository** and create a branch from `main`
2. **Use descriptive branch names:**
   - `feat/feature-name` for new features
   - `fix/bug-name` for bug fixes
   - `docs/doc-name` for documentation
   - `refactor/component-name` for refactoring
3. **Write or update tests** for your changes
4. **Run linting and tests** locally to ensure everything passes
5. **Update CHANGELOG.md** under `[Unreleased]` section (see Changelog Updates below)
6. **Submit a pull request** with:
   - A clear title following commit message format: `type: description`
   - Description of what changed and why
   - Links to relevant issues

PRs are reviewed by maintainers. We aim to review within a few days.

## Commit Message Guidelines

Use conventional commit format:

```
type: brief description

Optional longer explanation.
```

**Types:**
- `feat` — New feature
- `fix` — Bug fix
- `docs` — Documentation changes
- `refactor` — Code restructuring without behavior change
- `test` — Adding or updating tests
- `chore` — Maintenance tasks

**Examples:**

```
feat: add DuckDuckGo search provider integration

fix: handle arXiv PDF timeout gracefully

docs: clarify browser path setup in GETTING-STARTED.md

refactor(search): extract RRF merge into dedicated module
```

**Guidelines:**
- Keep the first line under 72 characters
- Reference issues with `#number` when applicable
- Use imperative mood: "add feature" not "added feature"

## Review Process

**Self-review checklist before submitting:**

- [ ] Tests pass: `pytest`
- [ ] Code formatted: `ruff format src/`
- [ ] No linting errors: `ruff check src/`
- [ ] CHANGELOG.md updated under `[Unreleased]`
- [ ] Environment variables documented in `settings.py` if new ones added
- [ ] Docstrings added for new public functions/classes

**After review:**

- Address review comments promptly
- Keep commits focused — avoid mixing unrelated changes
- Maintain clean commit history (avoid "fix typo" commits; amend before push)

## Changelog Updates

All changes must be documented in [CHANGELOG.md](./CHANGELOG.md) under `[Unreleased]`:

1. Add entries to the appropriate category:
   - `Added` — New features
   - `Changed` — Changes to existing functionality
   - `Fixed` — Bug fixes
   - `Deprecated` — Features being removed
   - `Removed` — Features removed
   - `Security` — Security-related changes

2. Follow the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format

3. Include PR/issue references when applicable: `feat: add X (#123)`

**Example entry:**

```markdown
### Added

- New `youtube_transcript` tool for extracting captions from YouTube videos
  with language selection and timestamped output formats (#42)
```

## Reporting Bugs

Report bugs via GitHub Issues: https://github.com/Shelpuk-AI-Technology-Consulting/kindly-web-search-mcp-server/issues

**Please include:**

- Clear description of the bug
- Steps to reproduce
- Expected behavior vs actual behavior
- Your environment (OS, Python version, MCP client)
- Relevant logs or error messages (enable diagnostics with `KINDLY_DIAGNOSTICS=1`)

## Feature Requests

Submit feature requests via GitHub Issues. Describe:

- The feature you want
- Why it would be useful
- Any implementation ideas you have

## License

This project is licensed under the MIT License. By contributing, you agree that your contributions will be licensed under the same MIT License.