"""Provider adapter implementations — each file registers itself on import."""

from __future__ import annotations

# Import adapters to trigger self-registration
from . import openai as _openai
from . import hf_chat as _hf_chat
from . import genai as _genai
from . import cohere as _cohere
from . import voyage as _voyage
from . import openrouter as _openrouter
