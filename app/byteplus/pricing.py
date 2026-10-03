"""Local, approximate pricing for the BytePlus Seedance 2.0 snapshot."""

from __future__ import annotations

from typing import Any


# BytePlus publishes Seedance 2.0's enhanced unit rate as USD 0.303 per
# billed second. These resolution factors are the documented calculator
# factors for the model snapshot used by this integration.
UNIT_PRICE_USD_PER_SECOND = 0.303
OUTPUT_FACTORS = {"480p": 0.4651, "720p": 1.0, "1080p": 2.5, "4k": 5.0761}
INPUT_VIDEO_FACTORS = {"480p": 0.2830, "720p": 0.6098, "1080p": 1.5251, "4k": 3.1348}


def estimate_cost(snapshot: dict[str, Any]) -> float:
    """Return an approximate USD cost for one Seedance 2.0 generation."""

    resolution = str(snapshot.get("resolution") or "720p").lower()
    try:
        factor = OUTPUT_FACTORS[resolution]
    except KeyError as exc:
        raise ValueError(f"Unsupported pricing resolution: {resolution}") from exc
    output_duration = float(snapshot.get("duration") or 0)
    if output_duration <= 0:
        raise ValueError("Video duration must be positive for cost estimation.")

    mode = str(snapshot.get("mode") or "text")
    input_duration = float(snapshot.get("input_video_duration") or 0)
    if mode in {"edit", "extend"} and input_duration > 0:
        factor = INPUT_VIDEO_FACTORS[resolution]
        billable_duration = input_duration + output_duration
    else:
        billable_duration = output_duration
    return round(UNIT_PRICE_USD_PER_SECOND * factor * billable_duration, 4)
