"""Content-type detection + per-type cleaning patterns.

Types: docs | wiki | blog | news | generic. Detection is heuristic (signal
phrases in the text); confidence is advisory. Each type carries: junk line
patterns, link-density tolerance, word floor/target, code expectation.
Used by the cleaner (type-specific junk patterns) and the scorer (type-
calibrated weights and floors).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

# ------------------------------------------------------------- junk patterns
GENERIC_PATTERNS = [
    r"^\s*skip to (main )?content\s*:?\s*$",
    r"^\s*(accept( all)? cookies|cookie (settings|consent|policy)|manage (cookie )?preferences|we use cookies)\b",
    r"^\s*(back|scroll) to top\s*$",
    r"^\s*(share (this|on)|tweet this|like this:)\b",
    r"^\s*(subscribe|sign ?up|follow us|join our (newsletter|mailing list))\b",
    r"^\s*(sign|log) ?in (to continue|now)\b",
    r"^\s*was this (page |article |doc(ument)? )?helpful\??.*$",
    r"^\s*(last )?(updated|modified|edited)( on)?:",
    r"^\s*(©|&copy;|copyright).*20\d\d",
    r"^\s*all rights reserved\s*\.?\s*$",
    r"^\s*(advertisement|sponsored|promoted)\s*$",
    r"^\s*\d+ min(ute)? read\s*$",
    r"^\s*(table of contents|on this page|in this article|contents)\s*:?\s*$",
    r"^\s*\d+ (replies|comments|answers|views)\s*$",
    r"^\s*(privacy policy|terms (of service|of use)|cookie policy)\s*$",
    r"^\s*language[:\s]*english\b",
    r"^\s*lorem ipsum\b",
    r"^\s*(download|get) (it )?now\b\s*$",
    r"^\s*menu\s*$",
]

DOCS_PATTERNS = [
    r"^\s*edit this page\b.*$",
    r"^\s*(suggest edits?|improve this page|report (an )?issue)\s*$",
    r"^\s*((previous|next)[\s|·›»]*)+$",
    r"^\s*(docs?|documentation|api reference|home)\s*›?\s*$",
    r"^\s*(was this (page )?helpful|did this (page )?help)\b",
    r"^\s*(on github|view source|view page source)\s*$",
    r"^\s*(copy|copy code|copy to clipboard|copied!?)\s*$",
]

WIKI_PATTERNS = [
    r"^\s*jump to (navigation|search)\s*$",
    r"^\s*jump to navigation ?jump to search\s*$",
    r"^\s*from wikipedia(, the free encyclopedia)?\s*$",
    r"^\s*retrieved from\b",
    r"^\s*this page was last edited on\b",
    r"^\s*categories?\s*:",
    r"^\s*navigation menu\s*$",
    r"^\s*(hidden categories?|wikimedia foundation)\b",
    r"^\s*(views|article|talk|read|edit|view history)\s*$",
    r"^\s*\[edit\]\s*$",
    r"^\s*citation needed\b",
]

BLOG_PATTERNS = [
    r"^\s*posted (in|on|by)\b",
    r"^\s*tags?\s*:",
    r"^\s*leave a (comment|reply)\s*$",
    r"^\s*\d+ (thoughts?|comments?) on\b",
    r"^\s*(related (posts|articles)|you might also like|further reading)\s*:?\s*$",
    r"^\s*about the author\s*$",
    r"^\s*(prev|next) (post|article)\s*$",
    r"^\s*powered by wordpress\s*$",
]

NEWS_PATTERNS = [
    r"^\s*(read|continue) (more|reading)\b",
    r"^\s*also read\b",
    r"^\s*(related|more (on|from)|top stories|most (read|popular))\b",
    r"^\s*(photo|image)s?\s*:\s*(ap|getty|reuters|afp)\b",
    r"^\s*(ap|afp|reuters|bloomberg|associated press)\s*[—–-]",
    r"^\s*this (article|story) (was|has been) (originally (published|appeared)|updated)\b",
    r"^\s*(follow|contact) (us|the (author|journalist))\b",
    r"^\s*(sign|log) in to (read|unlock)\b",
    r"^\s*(contribute|subscribe) (to|now)\b",
    r"^\s*all (rights reserved|quotes delayed)\b",
    r"^\s*trending\s*:?\s*$",
    r"^\s*(updated|published)\s*:?\s*\d{1,2}:\d{2}\b",
]


def _compile(pats: List[str]) -> Tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in pats)


@dataclass
class TypeProfile:
    name: str
    patterns: Tuple[re.Pattern, ...]
    linkd_tolerance: float      # expected max link-char density in body prose
    min_words: int              # below this => suspect
    target_words: int           # full credit around this
    code_expect: str            # "required" | "neutral"
    confidence: float = 0.0


PROFILES = {
    "docs":    TypeProfile("docs",    _compile(GENERIC_PATTERNS + DOCS_PATTERNS), 0.45, 120, 400, "required"),
    "wiki":    TypeProfile("wiki",    _compile(GENERIC_PATTERNS + WIKI_PATTERNS), 0.40, 150, 600, "neutral"),
    "blog":    TypeProfile("blog",    _compile(GENERIC_PATTERNS + BLOG_PATTERNS), 0.35, 120, 500, "neutral"),
    "news":    TypeProfile("news",    _compile(GENERIC_PATTERNS + NEWS_PATTERNS), 0.30, 100, 450, "neutral"),
    "generic": TypeProfile("generic", _compile(GENERIC_PATTERNS),                 0.38, 100, 350, "neutral"),
}

_WIKI_SIGNALS = [r"\[edit\]", r"citation needed", r"from wikipedia", r"retrieved from",
                 r"last edited on", r"wikimedia", r"infobox", r"\[\d{1,3}\]"]
_NEWS_SIGNALS = [r"\b(ap|afp|reuters|getty images|bloomberg)\b",
                 r"\bupdated (at|on)\b", r"\bpublished\b", r"\bby \w+ \w{3,}\b",
                 r"\b(20\d\d-\d\d-\d\d)\b", r"\breported that\b"]
_BLOG_SIGNALS = [r"\bmin(ute)? read\b", r"posted in", r"leave a (comment|reply)",
                 r"share this", r"\b\d+ comments\b"]
_DOCS_SIGNALS = [r"edit this page", r"on this page", r"getting started", r"installation",
                 r"quickstart", r"api reference", r"parameters?", r"returns", r"examples?"]


def detect_content_type(text: str) -> Tuple[str, float]:
    """Return (type, confidence) from signal phrases in the first ~12k chars."""
    t = text[:12000].lower()
    scores = {
        "wiki":  sum(1 for p in _WIKI_SIGNALS if re.search(p, t)),
        "news":  sum(1 for p in _NEWS_SIGNALS if re.search(p, t)),
        "blog":  sum(1 for p in _BLOG_SIGNALS if re.search(p, t)),
        "docs":  sum(1 for p in _DOCS_SIGNALS if re.search(p, t)) + min(3, t.count("```") // 2),
    }
    best = max(scores, key=scores.get)
    hits = scores[best]
    if hits == 0:
        return "generic", 0.0
    return best, min(1.0, hits / 4.0)


def profile_for(name: str) -> TypeProfile:
    return PROFILES.get(name, PROFILES["generic"])
