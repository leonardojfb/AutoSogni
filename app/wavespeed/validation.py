from __future__ import annotations

from typing import Any

from pathlib import Path

MODEL_ID = "alibaba/wan-3.0/reference-to-video"
SEEDANCE_MODEL_ID = "bytedance/seedance-2.0/text-to-video"
SEEDANCE_I2V_SPICY_MODEL_ID = "bytedance/seedance-2.0/image-to-video-spicy"
FLUX_MODEL_ID = "wavespeed-ai/flux-2-klein-9b/edit"
FACE_SWAP_MODEL_ID = "wavespeed-ai/image-face-swap"
RESOLUTIONS = ("480p", "720p", "1080p")
ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4")
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "timeout", "deleted"})
VIDEO_MAX_BYTES = 100 * 1024 * 1024


def validate_local_reference_file(path: Path, kind: str) -> None:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Reference file does not exist: {path}")
    suffix = path.suffix.lower()
    if kind == "reference_videos":
        if suffix not in {".mp4", ".mov"}:
            raise ValueError("Reference videos must be MP4 or MOV files.")
        if path.stat().st_size > VIDEO_MAX_BYTES:
            raise ValueError("Each reference video must not exceed 100 MB.")
    elif kind == "reference_images" and suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise ValueError("Reference images must be PNG, JPEG, WebP, or GIF files.")
    elif kind == "reference_audios" and suffix not in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}:
        raise ValueError("Reference audios must be MP3, WAV, M4A, AAC, or OGG files.")


def validate_request(payload: dict[str, Any], webhook_url: str | None = None) -> None:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Prompt is required.")

    reference_images = payload.get("reference_images") or []
    reference_videos = payload.get("reference_videos") or []
    reference_audios = payload.get("reference_audios") or []
    if not any((reference_images, reference_videos, reference_audios)):
        raise ValueError("At least one reference image, video, or audio is required.")
    if len(reference_images) > 10:
        raise ValueError("A maximum of 10 reference images is supported.")
    if len(reference_videos) > 5:
        raise ValueError("A maximum of 5 reference videos is supported.")
    if len(reference_audios) > 5:
        raise ValueError("A maximum of 5 reference audios is supported.")

    resolution = payload.get("resolution", "720p")
    if resolution not in RESOLUTIONS:
        raise ValueError(f"Resolution must be one of: {', '.join(RESOLUTIONS)}.")
    aspect_ratio = payload.get("aspect_ratio", "16:9")
    if aspect_ratio not in ASPECT_RATIOS:
        raise ValueError(f"Aspect ratio must be one of: {', '.join(ASPECT_RATIOS)}.")

    try:
        duration = int(payload.get("duration", 5))
    except (TypeError, ValueError) as exc:
        raise ValueError("Duration must be an integer from 2 to 30 seconds.") from exc
    if not 2 <= duration <= 30:
        raise ValueError("Duration must be an integer from 2 to 30 seconds.")

    seed = payload.get("seed")
    if seed is not None:
        try:
            seed = int(seed)
        except (TypeError, ValueError) as exc:
            raise ValueError("Seed must be -1 or an integer from 0 to 2147483647.") from exc
        if seed != -1 and not 0 <= seed <= 2147483647:
            raise ValueError("Seed must be -1 or an integer from 0 to 2147483647.")

    if reference_videos:
        total_reference_duration = payload.get("reference_video_duration_seconds")
        if total_reference_duration is not None:
            try:
                total_reference_duration = float(total_reference_duration)
            except (TypeError, ValueError) as exc:
                raise ValueError("Reference video duration must be numeric.") from exc
            if total_reference_duration > 15:
                raise ValueError("Total reference video duration must not exceed 15 seconds.")
            if total_reference_duration + duration > 30:
                raise ValueError("The output duration plus reference video duration must not exceed 30 seconds.")

    if webhook_url and payload.get("enable_sync_mode"):
        raise ValueError("Sync mode cannot be combined with a webhook.")
    if webhook_url and payload.get("enable_base64_output"):
        raise ValueError("Base64 output cannot be combined with a webhook.")


def build_reference_video_payload(
    *,
    prompt: str,
    reference_images: list[str] | None = None,
    reference_videos: list[str] | None = None,
    reference_audios: list[str] | None = None,
    resolution: str = "720p",
    aspect_ratio: str = "16:9",
    duration: int = 5,
    enable_prompt_expansion: bool = False,
    enable_audio: bool = True,
    seed: int | None = -1,
    enable_sync_mode: bool = False,
    enable_base64_output: bool = False,
    reference_video_duration_seconds: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prompt": prompt,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "enable_prompt_expansion": bool(enable_prompt_expansion),
        "enable_audio": bool(enable_audio),
        "enable_sync_mode": bool(enable_sync_mode),
        "enable_base64_output": bool(enable_base64_output),
    }
    if reference_images:
        payload["reference_images"] = list(reference_images)
    if reference_videos:
        payload["reference_videos"] = list(reference_videos)
    if reference_audios:
        payload["reference_audios"] = list(reference_audios)
    if seed is not None:
        payload["seed"] = int(seed)
    if reference_video_duration_seconds is not None:
        payload["reference_video_duration_seconds"] = reference_video_duration_seconds

    validate_request(payload)
    payload.pop("reference_video_duration_seconds", None)
    return payload


def build_seedance_payload(
    *, prompt: str, reference_images: list[str] | None = None,
    reference_videos: list[str] | None = None, reference_audios: list[str] | None = None,
    resolution: str = "720p", aspect_ratio: str = "16:9", duration: int = 5,
    enable_web_search: bool = False, generate_audio: bool = True, safety_checker: bool = True,
) -> dict[str, Any]:
    if not prompt.strip():
        raise ValueError("Prompt is required.")
    if resolution not in (*RESOLUTIONS, "4k"):
        raise ValueError("Invalid Seedance resolution.")
    if aspect_ratio not in (*ASPECT_RATIOS, "21:9"):
        raise ValueError("Invalid Seedance aspect ratio.")
    if not 4 <= duration <= 15:
        raise ValueError("Seedance duration must be 4 to 15 seconds.")
    references = {
        "reference_images": reference_images or [],
        "reference_videos": reference_videos or [],
        "reference_audios": reference_audios or [],
    }
    for kind, limit in (("reference_images", 9), ("reference_videos", 3), ("reference_audios", 3)):
        if len(references[kind]) > limit:
            raise ValueError(f"Seedance supports at most {limit} {kind}.")
    payload: dict[str, Any] = {
        "prompt": prompt, "resolution": resolution, "aspect_ratio": aspect_ratio,
        "duration": duration, "enable_web_search": bool(enable_web_search),
        "generate_audio": bool(generate_audio),
        "safety_checker": bool(safety_checker),
    }
    payload.update({kind: values for kind, values in references.items() if values})
    return payload


def build_seedance_i2v_spicy_payload(
    *, image: str, prompt: str = "", last_image: str | None = None,
    aspect_ratio: str = "", resolution: str = "720p", duration: int = 5,
    generate_audio: bool = True, seed: int | None = None, safety_checker: bool = True,
) -> dict[str, Any]:
    image = str(image or "").strip()
    if not image.startswith(("https://", "http://")):
        raise ValueError("Seedance Spicy requires a public start-image URL.")
    if last_image:
        last_image = str(last_image).strip()
        if not last_image.startswith(("https://", "http://")):
            raise ValueError("Seedance Spicy last_image must be a public image URL.")
    if aspect_ratio and aspect_ratio not in ASPECT_RATIOS + ("21:9",):
        raise ValueError("Invalid Seedance Spicy aspect ratio.")
    if resolution not in (*RESOLUTIONS, "4k"):
        raise ValueError("Invalid Seedance Spicy resolution.")
    try:
        duration = int(duration)
    except (TypeError, ValueError) as exc:
        raise ValueError("Seedance Spicy duration must be an integer from 4 to 15 seconds.") from exc
    if not 4 <= duration <= 15:
        raise ValueError("Seedance Spicy duration must be 4 to 15 seconds.")

    payload: dict[str, Any] = {
        "image": image,
        "resolution": resolution,
        "duration": duration,
        "generate_audio": bool(generate_audio),
        "safety_checker": bool(safety_checker),
    }
    if prompt.strip():
        payload["prompt"] = prompt.strip()
    if last_image:
        payload["last_image"] = last_image
    if aspect_ratio:
        payload["aspect_ratio"] = aspect_ratio
    if seed is not None:
        try:
            seed = int(seed)
        except (TypeError, ValueError) as exc:
            raise ValueError("Seedance Spicy seed must be -1 or an integer from 0 to 2147483647.") from exc
        if seed != -1 and not 0 <= seed <= 2_147_483_647:
            raise ValueError("Seedance Spicy seed must be -1 or an integer from 0 to 2147483647.")
        payload["seed"] = seed
    return payload


def build_flux_payload(*, prompt: str, images: list[str], size: str = "", seed: int = -1,
                       enable_sync_mode: bool = False, enable_base64_output: bool = False) -> dict[str, Any]:
    if not prompt.strip():
        raise ValueError("Prompt is required.")
    if not 1 <= len(images) <= 3:
        raise ValueError("Flux requires 1 to 3 images.")
    if seed < -1 or seed > 2_147_483_647:
        raise ValueError("Invalid Flux seed.")
    payload: dict[str, Any] = {"prompt": prompt, "images": list(images), "seed": seed,
                               "enable_sync_mode": bool(enable_sync_mode),
                               "enable_base64_output": bool(enable_base64_output)}
    if size.strip():
        payload["size"] = size.strip()
    return payload


def build_face_swap_payload(*, image: str, face_image: str, target_index: int = 0,
                            target_gender: str = "all", output_format: str = "png",
                            enable_sync_mode: bool = False,
                            enable_base64_output: bool = False) -> dict[str, Any]:
    if not image.strip():
        raise ValueError("Face Swap requires a base image.")
    if not face_image.strip():
        raise ValueError("Face Swap requires an identity face image.")
    if not 0 <= target_index <= 10:
        raise ValueError("Face Swap target index must be from 0 to 10.")
    if target_gender not in {"all", "male", "female"}:
        raise ValueError("Face Swap target gender must be all, male, or female.")
    if output_format not in {"jpeg", "png", "webp"}:
        raise ValueError("Face Swap output format must be jpeg, png, or webp.")
    return {
        "image": image, "face_image": face_image, "target_index": target_index,
        "target_gender": target_gender, "output_format": output_format,
        "enable_sync_mode": bool(enable_sync_mode),
        "enable_base64_output": bool(enable_base64_output),
    }
