---
type: source
title: "Observation: ProviderDefinition dual declaration fix"
slug: obs-2026-07-17-providerdefinition-dual-declaration-fix
status: observation
created: 2026-07-17
updated: 2026-07-17
relevance: high
observed_at: 2026-07-17T11:15:36.879Z
tags: ["search", "provider_catalog", "provider_registry", "refactor", "dual-declaration"]
source_context: "Planning search package refactor"
---
# ⭐ Observation: ProviderDefinition dual declaration fix
Discovered in search/architecture session 2026-07-17: `provider_catalog.py` and `provider_registry.py` declare every provider twice. `PROVIDER_DEFINITIONS_LIST` holds `_definition("name", group, ...)` for 21 entries; `_ADAPTER_PATHS` holds `{"name": ("module", "function")}` for the same 21. Sync is enforced only at import time via `RuntimeError` at `provider_registry.py:139-140`. Adding a new provider forces a two-file edit. Refactor: add `adapter_module` and `adapter_function` fields to `ProviderDefinition`; build `PROVIDER_ADAPTERS` directly from the catalog list. Eliminates the dual-typing invariant. Plan: local://search-package-refactor-langsearch-plan.md step 3.
*Relevance: high*

*Context: Planning search package refactor*

*Tags: search provider_catalog provider_registry refactor dual-declaration*
---
*Observed: 2026-07-17T11:15:36.879Z*