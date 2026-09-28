from __future__ import annotations

from typing import Any


def validate_lora_selection(
    model_id: str, selection: list[list[Any]], catalog: list[dict[str, Any]]
) -> list[list[Any]]:
    """Validate an ordered H3 LoRA stack against the current model catalog."""
    if not model_id.startswith("minimax-h3-"):
        raise ValueError("LoRAs are only available for MiniMax H3 models.")
    if len(selection) > 8:
        raise ValueError("A render can use at most 8 LoRAs.")
    available = {row.get("loraId"): row for row in catalog if model_id in row.get("modelIds", [])}
    result = []
    seen = set()
    for item in selection:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("Each LoRA needs an ID and strength.")
        lora_id, strength = item
        if lora_id not in available:
            raise ValueError(f"LoRA {lora_id!r} is not compatible with {model_id}.")
        if lora_id in seen:
            raise ValueError(f"LoRA {lora_id!r} was selected twice.")
        seen.add(lora_id)
        ui = available[lora_id].get("ui") or {}
        try:
            strength = float(strength)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid strength for LoRA {lora_id!r}.") from exc
        minimum = max(float(ui.get("min", 0)), 0.0)
        maximum = float(ui.get("max", 1))
        if lora_id.startswith("personal-"):
            minimum = max(minimum, 0.0)
            maximum = min(maximum, 1.0)
        if not minimum <= strength <= maximum or strength <= 0:
            raise ValueError(f"LoRA {lora_id!r} strength must be greater than 0 and at most {maximum}.")
        result.append([lora_id, strength])
    return result
