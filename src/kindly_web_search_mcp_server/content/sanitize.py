import re


_BOILERPLATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)^\s*share\s+(this|on\s+.*)\s*$"),
    re.compile(r"(?i)^\s*sign\s+up\s+for\s+(our\s+)?newsletter\s*$"),
    re.compile(r"(?i)^\s*related\s+(articles|posts|stories|videos)\s*$"),
    re.compile(r"(?i)^\s*leave\s+a\s+(comment|reply)\s*$"),
    re.compile(r"(?i)^\s*cookie\s+(settings|preferences|policy)\s*$"),
    re.compile(r"(?i)^\s*follow\s+us\s+on\s+.*\s*$"),
    re.compile(r"(?i)^\s*subscribe\s+(now|today)?\s*$"),
)


def strip_boilerplate(markdown: str) -> str:
    """Remove common noisy/boilerplate lines from extracted markdown."""
    if not markdown:
        return markdown
    lines = markdown.splitlines()
    filtered = [
        line for line in lines if not any(pattern.search(line) for pattern in _BOILERPLATE_PATTERNS)
    ]
    return "\n".join(filtered)


def sanitize_markdown(markdown: str) -> str:
    """
    Cleans up the markdown content by removing excessive newlines and whitespace.
    """
    # Replace multiple newlines with a single one
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    # Replace multiple spaces with a single one, but not spaces at the start of a line
    markdown = re.sub(r"(?<!^)[ ]{2,}", " ", markdown)
    # Remove leading/trailing whitespace from each line
    markdown = "\n".join(line.strip() for line in markdown.split("\n"))
    return markdown.strip()
