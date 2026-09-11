"""Candidate non-LLM query-refinement technique implementations.

Every function here is a thin, dependency-isolated wrapper so the evaluation
harness can measure each technique's accuracy/latency independently and the
final report can cite exact library calls used.
"""
from __future__ import annotations

import re
import time

# ---------------------------------------------------------------------------
# 1. Glued / concatenated word segmentation (wordninja)
# ---------------------------------------------------------------------------
import wordninja


def segment_glued(text: str) -> list[str]:
    """Split a single glued token into likely constituent words.

    wordninja uses a precomputed English unigram/bigram frequency corpus
    (derived from a large web text corpus) with a dynamic-programming
    (Viterbi-style) split -- language-general, no per-domain training required.
    """
    return wordninja.split(text)


# ---------------------------------------------------------------------------
# 2. Spell correction: rapidfuzz (edit-distance against a reference lexicon)
#    vs symspellpy (Symmetric Delete algorithm, frequency dictionary lookup)
# ---------------------------------------------------------------------------
from rapidfuzz import fuzz, process
from symspellpy import SymSpell
import importlib.resources as pkg_resources

_sym_spell: SymSpell | None = None


def _get_symspell() -> SymSpell:
    global _sym_spell
    if _sym_spell is None:
        sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
        dict_path = pkg_resources.files("symspellpy") / "frequency_dictionary_en_82_765.txt"
        sym.load_dictionary(str(dict_path), term_index=0, count_index=1)
        _sym_spell = sym
    return _sym_spell


def correct_spelling_symspell(text: str) -> str:
    """Whole-phrase correction using SymSpell's compound lookup (max edit distance 2)."""
    sym = _get_symspell()
    suggestions = sym.lookup_compound(text, max_edit_distance=2, transfer_casing=True)
    return suggestions[0].term if suggestions else text


_RAPIDFUZZ_VOCAB_CACHE: list[str] | None = None


def _get_rapidfuzz_vocab() -> list[str]:
    """Reuse SymSpell's frequency dictionary word list as rapidfuzz's candidate
    vocabulary so both techniques are compared against an identical reference
    lexicon (fair, controlled comparison)."""
    global _RAPIDFUZZ_VOCAB_CACHE
    if _RAPIDFUZZ_VOCAB_CACHE is None:
        dict_path = pkg_resources.files("symspellpy") / "frequency_dictionary_en_82_765.txt"
        vocab = []
        with open(str(dict_path), encoding="utf-8") as fh:
            for line in fh:
                parts = line.strip().split()
                if parts:
                    vocab.append(parts[0])
        _RAPIDFUZZ_VOCAB_CACHE = vocab
    return _RAPIDFUZZ_VOCAB_CACHE


def correct_spelling_rapidfuzz(text: str, score_cutoff: float = 85.0) -> str:
    """Per-token nearest-neighbour correction against the reference vocabulary.

    Conservative gate: only replace a token if (a) it is NOT already in the
    vocabulary (avoid overcorrecting real words/entities/brand names) and
    (b) the best fuzzy match clears score_cutoff. This directly encodes the
    "dictionary spellcheckers overcorrect real search queries" risk found in
    literature review -- rapidfuzz here is deliberately more conservative
    than symspell's compound-lookup, which corrects every token unconditionally.
    """
    vocab_list = _get_rapidfuzz_vocab()
    vocab_set = set(vocab_list)
    tokens = text.split()
    out = []
    for tok in tokens:
        lower = tok.lower()
        if lower in vocab_set or not lower.isalpha() or len(lower) <= 3:
            out.append(tok)
            continue
        match = process.extractOne(lower, vocab_list, scorer=fuzz.ratio, score_cutoff=score_cutoff)
        out.append(match[0] if match else tok)
    return " ".join(out)


# ---------------------------------------------------------------------------
# 3. Language identification: langdetect vs langid vs lingua
# ---------------------------------------------------------------------------
import langdetect
import langid
from lingua import Language, LanguageDetectorBuilder

_LINGUA_LANGS = [Language.ENGLISH, Language.POLISH, Language.SPANISH, Language.GERMAN, Language.FRENCH]
_lingua_detector = LanguageDetectorBuilder.from_languages(*_LINGUA_LANGS).build()

_LINGUA_ISO = {
    Language.ENGLISH: "en", Language.POLISH: "pl", Language.SPANISH: "es",
    Language.GERMAN: "de", Language.FRENCH: "fr",
}


def detect_lang_langdetect(text: str) -> str:
    try:
        return langdetect.detect(text)
    except Exception:
        return "unknown"


def detect_lang_langid(text: str) -> str:
    lang, _score = langid.classify(text)
    return lang


def detect_lang_lingua(text: str) -> str:
    lang = _lingua_detector.detect_language_of(text)
    return _LINGUA_ISO.get(lang, "unknown") if lang else "unknown"


# ---------------------------------------------------------------------------
# 4. General-purpose synonym expansion: WordNet (nltk) -- NOT a technical
#    acronym thesaurus; this is the generalization-focused counterpart to the
#    hand-built ~17-entry technical thesaurus from the earlier report.
# ---------------------------------------------------------------------------
from nltk.corpus import wordnet as wn

_STOPWORDS = {
    "a", "an", "the", "of", "for", "to", "in", "on", "at", "is", "are", "and", "or",
    "near", "me", "my", "vs", "with", "how", "what", "why", "when", "where", "who",
}


def expand_synonyms_wordnet(text: str, max_per_token: int = 3) -> dict[str, list[str]]:
    """Return {content_word: [synonym, ...]} using WordNet lemma names.

    Skips stopwords and short/non-alphabetic tokens. Deduplicates and excludes
    the original word; multi-word lemma phrases have underscores collapsed
    to spaces.
    """
    out: dict[str, list[str]] = {}
    for tok in re.findall(r"[a-zA-Z]+", text.lower()):
        if tok in _STOPWORDS or len(tok) < 3:
            continue
        syns: list[str] = []
        seen = {tok}
        for synset in wn.synsets(tok):
            for lemma in synset.lemmas():
                name = lemma.name().replace("_", " ").lower()
                if name not in seen:
                    seen.add(name)
                    syns.append(name)
            if len(syns) >= max_per_token:
                break
        if syns:
            out[tok] = syns[:max_per_token]
    return out


# ---------------------------------------------------------------------------
# 5. Rule-based Broder-taxonomy intent classifier (informational / navigational
#    / transactional / commercial / local / comparative)
# ---------------------------------------------------------------------------
_NAV_BRAND_HINTS = re.compile(
    r"\b(login|sign ?in|logon|official (site|website)|homepage|customer service|"
    r"account (settings|update)|\.gov|\.com|download)\b", re.I
)
_TRANSACTIONAL_HINTS = re.compile(
    r"\b(buy|order|book|purchase|signup|sign up|subscribe|rent|hire|reserve|renew|"
    r"track my|open an? account|file taxes)\b", re.I
)
_COMMERCIAL_HINTS = re.compile(
    r"\b(best|top rated|top[- ]?\d*|cheapest|deals|review|alternative)\b", re.I
)
_LOCAL_HINTS = re.compile(
    r"\b(near me|nearby|nearest|open now)\b", re.I
)
_COMPARATIVE_HINTS = re.compile(r"\bvs\.?\b|\bversus\b|\bor\b.*\b(comparison|which|for)\b", re.I)
_INFORMATIONAL_HINTS = re.compile(
    r"\b(how (to|contagious|long|many)|what is|why|when (was|is|did)|symptoms of|"
    r"difference between|substitute for|requirements for|eligibility|rate for)\b", re.I
)


def classify_intent_rule_based(text: str) -> str:
    """Broder (2002) taxonomy + commercial-investigation extension, adapted from
    SERP-pattern rule-based classifiers (lexical-modifier signals only, no live
    SERP fetch required -- cheap first-pass; live SERP composition from ddgs
    is used separately as a calibration/validation signal, not a hard input)."""
    t = text.strip()
    if _NAV_BRAND_HINTS.search(t):
        return "navigational"
    if _COMPARATIVE_HINTS.search(t):
        return "comparative"
    if _TRANSACTIONAL_HINTS.search(t):
        return "transactional"
    if _LOCAL_HINTS.search(t):
        return "local"
    if _COMMERCIAL_HINTS.search(t):
        return "commercial"
    if _INFORMATIONAL_HINTS.search(t):
        return "informational"
    return "informational"  # default fallback, matches Broder's dominant-class prior


# ---------------------------------------------------------------------------
# Timing helper
# ---------------------------------------------------------------------------
def timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, (time.perf_counter() - t0) * 1000.0
