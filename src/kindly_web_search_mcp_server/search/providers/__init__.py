"""Provider adapters for web search.

Each module exposes an async ``search_<name>`` function that implements a single
search backend. Adapters are registered in ``search/provider_catalog.py`` and
wired to the runtime via ``search/provider_registry.py``.
"""
