from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WaveSpeedPrediction:
    id: str
    status: str
    model: str = ""
    outputs: list[Any] = field(default_factory=list)
    urls: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: str = ""
    timings: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "WaveSpeedPrediction":
        return cls(
            id=str(payload.get("id") or ""),
            status=str(payload.get("status") or ""),
            model=str(payload.get("model") or ""),
            outputs=list(payload.get("outputs") or []),
            urls=dict(payload.get("urls") or {}),
            error=str(payload.get("error") or ""),
            created_at=str(payload.get("created_at") or ""),
            timings=dict(payload.get("timings") or {}),
            raw=dict(payload),
        )
