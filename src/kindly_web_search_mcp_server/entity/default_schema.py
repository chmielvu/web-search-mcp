"""Shared GLiNER2 entity and relation descriptions."""

from __future__ import annotations

# One vocabulary is shared by query understanding and content extraction.
DEFAULT_QUERY_LABELS: dict[str, str] = {
    "package": "Software package, library, or framework name",
    "version": "Software version string",
    "api_function": "API endpoint, function, or method name",
    "error_class": "Error or exception class name",
    "repo_ref": "GitHub or GitLab repository reference",
    "cli_flag": "Command-line flag or argument",
    "model_id": "Machine-learning model identifier",
    "file_path": "File or module path",
    "env_var": "Environment variable name",
    "person": "Person name",
    "organization": "Company, team, or organization",
    "date": "Date or time expression",
    "product": "Product, service, or platform product name",
    "url": "URL or web address",
    "language": "Programming, markup, or data language",
    "platform": "Operating system, runtime, hosting platform, or target environment",
    "provider": "Cloud, model, search, or API provider",
    "dataset": "Dataset or corpus name",
    "topic": "Named subject or technical topic",
    "tool": "Developer or command-line tool",
}

DEFAULT_QUERY_RELATIONS: dict[str, str] = {
    "compares_with": "One named software, model, provider, platform, product, or tool is compared with another",
    "version_of": "A package, product, or model is associated with its version",
    "uses": "A project, package, framework, or tool uses another package, API, model, or tool",
    "requires": "A package, project, or tool requires a dependency, version, API, or environment variable",
    "runs_on": "A package, model, or tool runs on or targets a platform, runtime, operating system, or provider",
    "implements": "A package, project, or framework implements an API, protocol, or interface",
}

# Content extraction reuses every query label and adds no second vocabulary.
DEFAULT_CONTENT_LABELS: dict[str, str] = dict(DEFAULT_QUERY_LABELS)
DEFAULT_CONTENT_RELATIONS: dict[str, str] = dict(DEFAULT_QUERY_RELATIONS)
