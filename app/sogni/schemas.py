from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelDescriptor:
    id: str
    name: str
    media_type: str
    parameters: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "ModelDescriptor":
        return cls(
            id=str(payload.get("id", "")),
            name=str(payload.get("name") or payload.get("id") or "Unnamed model"),
            media_type=str(payload.get("mediaType") or payload.get("media_type") or ""),
            parameters=payload.get("parameters") or {},
        )
