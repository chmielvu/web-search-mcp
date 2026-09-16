"""Provider adapter implementations — each file registers itself on import."""

from __future__ import annotations

from . import genai as _genai
from . import hf_chat as _hf_chat

# Import adapters to trigger self-registration
from . import openai as _openai
from . import voyage as _voyage
