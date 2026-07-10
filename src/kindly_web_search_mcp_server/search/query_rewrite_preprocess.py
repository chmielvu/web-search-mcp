from __future__ import annotations

from dataclasses import dataclass, field
from html import escape


@dataclass(frozen=True, slots=True)
class RewritePreprocessSignals:
    normalized_query: str
    brave_suggestions: list[str] = field(default_factory=list)
    brave_entities: list[dict[str, str]] = field(default_factory=list)
    brave_spellcheck: str | None = None
    must_keep_terms: list[str] = field(default_factory=list)

    def prompt_block(self) -> str:
        parts = [f"<normalized_query>{escape(self.normalized_query)}</normalized_query>"]
        if self.brave_spellcheck and self.brave_spellcheck != self.normalized_query:
            parts.append(
                f"<spellcheck_correction>{escape(self.brave_spellcheck)}</spellcheck_correction>"
            )
        if self.brave_suggestions:
            parts.append("<brave_autosuggest>")
            parts.extend(f"  <suggestion>{escape(suggestion)}</suggestion>" for suggestion in self.brave_suggestions)
            parts.append("</brave_autosuggest>")
        if self.brave_entities:
            parts.append("<brave_entities>")
            for entity in self.brave_entities:
                name = entity.get("name", "").strip()
                if not name:
                    continue
                description = entity.get("description", "").strip()
                description_attr = f' description="{escape(description, quote=True)}"' if description else ""
                parts.append(f"  <entity{description_attr}>{escape(name)}</entity>")
            parts.append("</brave_entities>")
        if self.must_keep_terms:
            parts.append("<must_keep_terms>")
            parts.extend(f"  <term>{escape(term)}</term>" for term in self.must_keep_terms)
            parts.append("</must_keep_terms>")
        return "\n".join(parts)


def build_rewrite_preprocess_signals(
    normalized_query: str,
    *,
    brave_suggestions: list[str] | None = None,
    brave_entities: list[dict[str, str]] | None = None,
    brave_spellcheck: str | None = None,
    must_keep_terms: list[str] | None = None,
) -> RewritePreprocessSignals:
    """Construct an immutable signal bundle for rewrite prompt rendering."""
    return RewritePreprocessSignals(
        normalized_query=normalized_query,
        brave_suggestions=list(brave_suggestions or []),
        brave_entities=list(brave_entities or []),
        brave_spellcheck=brave_spellcheck,
        must_keep_terms=list(must_keep_terms or []),
    )
