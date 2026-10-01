from __future__ import annotations

from typing import Any

MODEL_ID = "dreamina-seedance-2-0-260128"
RESOLUTIONS = ("480p", "720p", "1080p", "4k")
RATIOS = ("adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
MODES = ("text", "first_frame", "first_last_frame", "omni", "edit", "extend")


def _url(value: object, label: str) -> str:
    value = str(value or "").strip()
    if not value.startswith(("https://", "http://", "asset://")):
        raise ValueError(f"{label} must be a public URL or BytePlus asset URL.")
    return value


def _content(type_: str, url: str, role: str) -> dict[str, Any]:
    return {"type": type_, type_: {"url": url}, "role": role}


def build_task_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    mode = str(snapshot.get("mode") or "text")
    if mode not in MODES:
        raise ValueError("Unsupported BytePlus generation mode.")
    prompt = str(snapshot.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Prompt is required.")
    resolution = str(snapshot.get("resolution") or "720p")
    ratio = str(snapshot.get("ratio") or "16:9")
    duration = int(snapshot.get("duration") or 5)
    if resolution not in RESOLUTIONS or ratio not in RATIOS or not 4 <= duration <= 15:
        raise ValueError("Resolution, ratio, or duration is invalid for Seedance 2.0.")
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if mode in {"first_frame", "first_last_frame"}:
        if resolution == "1080p":
            raise ValueError("1080p is not supported for image-reference tasks.")
        content.append(_content("image_url", _url(snapshot.get("first_frame_url"), "First frame"), "first_frame"))
        if mode == "first_last_frame":
            content.append(_content("image_url", _url(snapshot.get("last_frame_url"), "Last frame"), "last_frame"))
    if mode == "omni":
        images = list(snapshot.get("reference_images") or [])
        videos = list(snapshot.get("reference_videos") or [])
        audios = list(snapshot.get("reference_audios") or [])
        if not images and not videos:
            raise ValueError("Omni references require an image or video; audio cannot be used alone.")
        if len(images) > 9 or len(videos) > 3 or len(audios) > 3:
            raise ValueError("Seedance 2.0 supports at most 9 images, 3 videos, and 3 audios.")
        content += [_content("image_url", _url(value, "Reference image"), "reference_image") for value in images]
        content += [_content("video_url", _url(value, "Reference video"), "reference_video") for value in videos]
        content += [_content("audio_url", _url(value, "Reference audio"), "reference_audio") for value in audios]
    if mode in {"edit", "extend"}:
        content.append(_content("video_url", _url(snapshot.get("reference_video_url"), "Reference video"), "reference_video"))
    payload: dict[str, Any] = {"model": MODEL_ID, "content": content, "generate_audio": bool(snapshot.get("generate_audio", True)),
                               "resolution": resolution, "ratio": ratio, "duration": duration,
                               "watermark": bool(snapshot.get("watermark", False)), "return_last_frame": bool(snapshot.get("return_last_frame", False))}
    for key in ("seed", "callback_url", "execution_expires_after"):
        if snapshot.get(key) not in (None, ""):
            payload[key] = snapshot[key]
    if "seed" in payload and not 0 <= int(payload["seed"]) <= 2_147_483_647:
        raise ValueError("Seed must be from 0 to 2147483647.")
    if "execution_expires_after" in payload and not 3600 <= int(payload["execution_expires_after"]) <= 259200:
        raise ValueError("Execution expiry must be from 3600 to 259200 seconds.")
    if mode in {"edit", "extend"}:
        payload["omni_reference_task_type"] = mode
    return payload
