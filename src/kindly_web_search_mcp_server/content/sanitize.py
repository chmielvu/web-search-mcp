import re


from ..heuristics.text_clean import clean_text_for_llm


_BOILERPLATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)^\s*share\s+(this|on\s+.*)\s*$"),
    re.compile(r"(?i)^\s*sign\s+up\s+for\s+(our\s+)?newsletter\s*$"),
    re.compile(r"(?i)^\s*related\s+(articles|posts|stories|videos)\s*$"),
    re.compile(r"(?i)^\s*leave\s+a\s+(comment|reply)\s*$"),
    re.compile(r"(?i)^\s*cookie\s+(settings|preferences|policy)\s*$"),
    re.compile(r"(?i)^\s*follow\s+us\s+on\s+.*\s*$"),
    re.compile(r"(?i)^\s*subscribe\s+(now|today)?\s*$"),
    re.compile(r"(?i)^\s*advertisement\s*$"),
    re.compile(r"(?i)^\s*\*{0,4}\s*hide caption\s*\*{0,4}\s*$"),
    re.compile(r"(?i)^\s*\*{0,4}\s*toggle caption\s*\*{0,4}\s*$"),
    re.compile(r"(?i)^\s*site search\s*$"),
    re.compile(r"(?i)^\s*more to explore\s*$"),
    re.compile(r"(?i)^\s*most watched\s*$"),
    re.compile(r"(?i)^\s*most read\s*$"),
    re.compile(r"(?i)^\s*also in news\s*$"),
    re.compile(r"^\s*[\*\-]\s*$"),
    re.compile(r"(?i)^\s*not yet fully loaded\s*$"),
)

_EMPTY_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*\)")


def _collapse_duplicate_lines(lines: list[str]) -> list[str]:
    collapsed: list[str] = []
    index = 0
    while index < len(lines):
        end = index + 1
        while end < len(lines) and lines[end] == lines[index]:
            end += 1
        run = end - index
        if run >= 3:
            collapsed.append(lines[index])
        else:
            collapsed.extend(lines[index:end])
        index = end
    return collapsed


def strip_boilerplate(markdown: str) -> str:
    """Remove common noisy/boilerplate lines from extracted markdown."""
    if not markdown:
        return markdown
    lines = markdown.splitlines()
    filtered = [
        line for line in lines if not any(pattern.search(line) for pattern in _BOILERPLATE_PATTERNS)
    ]
    filtered = _collapse_duplicate_lines(filtered)
    return "\n".join(filtered)


def repair_empty_md_links(markdown: str) -> str:
    """Drop empty markdown hrefs, keeping the link text."""
    if not markdown:
        return markdown
    return _EMPTY_MD_LINK_RE.sub(r"\1", markdown)


def sanitize_markdown(markdown: str) -> str:
    """
    Cleans up the markdown content by removing excessive newlines and whitespace.
    """
    markdown = clean_text_for_llm(markdown, role="page")
    markdown = repair_empty_md_links(markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    markdown = re.sub(r"(?<!^)[ ]{2,}", " ", markdown)
    markdown = "\n".join(line.strip() for line in markdown.split("\n"))
    return markdown.strip()
