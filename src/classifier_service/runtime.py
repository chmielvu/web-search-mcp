"""GLiNER2 runtime helpers for the Cloud Run inference service."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

MODEL_ID = os.environ.get("KINDLY_GLINER_MODEL", "fastino/gliner2-base-v1")


def _as_str_list(value: Any, *, field_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list[str]")
    return [item.strip() for item in value if item.strip()]


def _as_str_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value


def _parse_field_spec(field_spec: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in field_spec.split("::")]
    if not parts or not parts[0]:
        raise ValueError("field specs must start with a field name")
    if len(parts) == 1:
        return parts[0], "str", ""
    if len(parts) == 2:
        return parts[0], parts[1] or "str", ""
    return parts[0], parts[1] or "str", "::".join(parts[2:]).strip()


@dataclass(slots=True)
class GLiNER2Runtime:
    """Lazy single-instance GLiNER2 wrapper."""

    model_id: str = MODEL_ID
    _extractor: Any | None = None
    _loaded_at: float = 0.0

    def load(self) -> None:
        if self._extractor is not None:
            return
        logger.info("Loading GLiNER2 model %s", self.model_id)
        from gliner2 import GLiNER2

        self._extractor = GLiNER2.from_pretrained(self.model_id)
        self._loaded_at = time.monotonic()
        logger.info("GLiNER2 ready")

    def ensure_loaded(self) -> Any:
        self.load()
        if self._extractor is None:
            raise RuntimeError("GLiNER2 failed to load")
        return self._extractor

    def health(self) -> dict[str, Any]:
        ready = self._extractor is not None
        return {
            "status": "ok" if ready else "warming_up",
            "model": self.model_id,
            "uptime_seconds": round(time.monotonic() - self._loaded_at, 3) if ready else 0.0,
        }

    def classify_text(
        self,
        text: str,
        labels: dict[str, list[str] | dict[str, str]],
    ) -> dict[str, Any]:
        extractor = self.ensure_loaded()
        normalized_labels: dict[str, list[str] | dict[str, str]] = {}
        for task_name, task_labels in labels.items():
            if isinstance(task_labels, list):
                normalized_labels[task_name] = _as_str_list(task_labels, field_name=f"labels[{task_name}]")
            elif isinstance(task_labels, dict):
                normalized_labels[task_name] = {
                    str(label): str(description).strip()
                    for label, description in task_labels.items()
                    if str(label).strip()
                }
            else:
                raise ValueError(f"labels[{task_name}] must be list[str] or object")
        return extractor.classify_text(text, normalized_labels)

    def extract_entities(
        self,
        text: str,
        entity_types: list[str] | dict[str, str],
        *,
        include_confidence: bool = False,
        include_spans: bool = False,
    ) -> dict[str, Any]:
        extractor = self.ensure_loaded()
        if isinstance(entity_types, list):
            normalized_entity_types: list[str] | dict[str, str] = _as_str_list(
                entity_types, field_name="entity_types"
            )
        elif isinstance(entity_types, dict):
            normalized_entity_types = {
                str(entity): str(description).strip()
                for entity, description in entity_types.items()
                if str(entity).strip()
            }
        else:
            raise ValueError("entity_types must be a list[str] or object")
        return extractor.extract_entities(
            text,
            normalized_entity_types,
            include_confidence=include_confidence,
            include_spans=include_spans,
        )

    def extract_json(
        self,
        text: str,
        structures: dict[str, list[str]],
        *,
        threshold: float = 0.5,
        include_confidence: bool = False,
        include_spans: bool = False,
    ) -> dict[str, Any]:
        extractor = self.ensure_loaded()
        normalized_structures: dict[str, list[str]] = {}
        for struct_name, field_specs in structures.items():
            normalized_structures[str(struct_name)] = [
                str(field_spec).strip() for field_spec in _as_str_list(field_specs, field_name=f"structures[{struct_name}]")
            ]
        return extractor.extract_json(
            text,
            normalized_structures,
            threshold=threshold,
            include_confidence=include_confidence,
            include_spans=include_spans,
        )

    def extract_relations(
        self,
        text: str,
        relations: list[str],
        *,
        include_confidence: bool = False,
        include_spans: bool = False,
    ) -> dict[str, Any]:
        extractor = self.ensure_loaded()
        normalized_relations = _as_str_list(relations, field_name="relations")
        return extractor.extract_relations(
            text,
            normalized_relations,
            include_confidence=include_confidence,
            include_spans=include_spans,
        )

    def extract_combined(
        self,
        text: str,
        *,
        entities: list[str] | dict[str, str] | None = None,
        classification: dict[str, dict[str, Any]] | None = None,
        structures: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        extractor = self.ensure_loaded()
        schema = extractor.create_schema()

        if entities:
            if isinstance(entities, list):
                schema = schema.entities(_as_str_list(entities, field_name="entities"))
            elif isinstance(entities, dict):
                schema = schema.entities(
                    {
                        str(entity): str(description).strip()
                        for entity, description in entities.items()
                        if str(entity).strip()
                    }
                )
            else:
                raise ValueError("entities must be a list[str] or object")

        if classification:
            for task_name, task_config in classification.items():
                labels = task_config.get("labels")
                if labels is None:
                    raise ValueError(f"classification[{task_name}] is missing labels")
                multi_label = bool(task_config.get("multi_label", False))
                cls_threshold = float(task_config.get("cls_threshold", 0.5))
                kwargs: dict[str, Any] = {
                    "multi_label": multi_label,
                    "cls_threshold": cls_threshold,
                }
                label_descriptions = task_config.get("label_descriptions")
                if isinstance(label_descriptions, dict):
                    kwargs["label_descriptions"] = {
                        str(label): str(description).strip()
                        for label, description in label_descriptions.items()
                        if str(label).strip()
                    }
                schema = schema.classification(
                    str(task_name),
                    _as_str_list(labels, field_name=f"classification[{task_name}].labels")
                    if isinstance(labels, list)
                    else {
                        str(label): str(description).strip()
                        for label, description in _as_str_mapping(
                            labels, field_name=f"classification[{task_name}].labels"
                        ).items()
                        if str(label).strip()
                    },
                    **kwargs,
                )

        if structures:
            for struct_name, field_specs in structures.items():
                struct_schema = schema.structure(str(struct_name))
                for field_spec in field_specs:
                    field_name, dtype, description = _parse_field_spec(str(field_spec))
                    kwargs: dict[str, Any] = {"dtype": dtype}
                    if description:
                        kwargs["description"] = description
                    struct_schema = struct_schema.field(field_name, **kwargs)
                schema = struct_schema

        if not any([entities, classification, structures]):
            raise ValueError("combined extraction requires entities, classification, or structures")

        return extractor.extract(text, schema)

