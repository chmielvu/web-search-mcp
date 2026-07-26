"""Chain specification and registry — ordered lists of model references."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .types import ModelSpec
from .registry import get_model

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChainSpec:
    name: str
    model_spec_ids: tuple[str, ...]

    @property
    def models(self) -> list[ModelSpec]:
        return [get_model(sid) for sid in self.model_spec_ids]

    @property
    def primary(self) -> ModelSpec:
        return get_model(self.model_spec_ids[0])

    @property
    def fallbacks(self) -> list[ModelSpec]:
        return [get_model(sid) for sid in self.model_spec_ids[1:]]


_CHAINS: dict[str, ChainSpec] = {}


def register_chain(name: str, model_spec_ids: list[str]) -> ChainSpec:
    if name in _CHAINS:
        logger.warning("Overwriting existing chain: %s", name)
    spec = ChainSpec(name=name, model_spec_ids=tuple(model_spec_ids))
    _CHAINS[name] = spec
    return spec


def get_chain(name: str) -> ChainSpec:
    if name not in _CHAINS:
        raise KeyError(f"Unknown chain: {name}")
    return _CHAINS[name]


def list_chains() -> list[str]:
    return list(_CHAINS.keys())
