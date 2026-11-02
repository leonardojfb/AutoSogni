"""Venice model metadata, request construction, and local image validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct


REQUESTED_MODEL_IDS = (
    "qwen-image-2-1-pro-edit",
    "nano-banana-pro-edit",
    "seedream-v5-pro-edit",
    "flux-2-max-edit",
)
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_PIXELS = 33_177_600
MIN_IMAGE_DIMENSION = 256
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SUPPORTED_OUTPUT_FORMATS = {"png", "jpeg", "webp"}


@dataclass(frozen=True)
class ModelProfile:
    id: str
    label: str
    max_input_images: int
    prompt_character_limit: int
    resolutions: tuple[str, ...]
    aspect_ratios: tuple[str, ...]
    default_resolution: str = ""


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height: int
    format: str
    size_bytes: int


def parse_model_catalog(payload: dict) -> dict[str, ModelProfile]:
    """Return requested edit models found in a Venice model catalog."""
    entries = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(entries, list):
        return {}
    profiles: dict[str, ModelProfile] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if model_id not in REQUESTED_MODEL_IDS:
            continue
        spec = entry.get("model_spec")
        constraints = spec.get("constraints") if isinstance(spec, dict) else None
        if not isinstance(constraints, dict):
            continue
        try:
            max_images = int(constraints["maxInputImages"])
            prompt_limit = int(constraints["promptCharacterLimit"])
            resolutions = tuple(str(value) for value in constraints.get("resolutions", ()))
            aspect_ratios = tuple(str(value) for value in constraints.get("aspectRatios", ()))
            default_resolution = str(constraints.get("defaultResolution") or "")
            label = str(spec.get("name") or model_id)
        except (KeyError, TypeError, ValueError):
            continue
        if max_images < 1 or prompt_limit < 1:
            continue
        profiles[model_id] = ModelProfile(
            id=model_id, label=label, max_input_images=max_images,
            prompt_character_limit=prompt_limit, resolutions=resolutions,
            aspect_ratios=aspect_ratios, default_resolution=default_resolution,
        )
    return {model_id: profiles[model_id] for model_id in REQUESTED_MODEL_IDS if model_id in profiles}


def build_multi_edit_payload(
    *, profile: ModelProfile, prompt: str, images: list[str],
    aspect_ratio: str = "", resolution: str = "", output_format: str = "png",
    safe_mode: bool = True,
) -> dict[str, object]:
    prompt_text = str(prompt)
    if not prompt_text.strip():
        raise ValueError("Prompt is required.")
    if len(prompt_text) > profile.prompt_character_limit:
        raise ValueError(f"Prompt must be at most {profile.prompt_character_limit} characters.")
    if not images:
        raise ValueError("At least one input image is required.")
    if len(images) > profile.max_input_images:
        raise ValueError(f"This model accepts at most {profile.max_input_images} input images.")
    if aspect_ratio and aspect_ratio not in profile.aspect_ratios:
        raise ValueError("Unsupported aspect ratio for this model.")
    if resolution and resolution not in profile.resolutions:
        raise ValueError("Unsupported resolution for this model.")
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError("Output format must be png, jpeg, or webp.")

    payload: dict[str, object] = {
        "modelId": profile.id,
        "prompt": prompt_text,
        "images": list(images),
    }
    if aspect_ratio:
        payload["aspect_ratio"] = aspect_ratio
    if resolution:
        payload["resolution"] = resolution
    payload["output_format"] = output_format
    payload["safe_mode"] = bool(safe_mode)
    return payload


def validate_prompt_and_images(*, profile: ModelProfile, prompt: str, image_paths: list[Path]) -> None:
    if not str(prompt).strip():
        raise ValueError("Prompt is required.")
    if len(prompt) > profile.prompt_character_limit:
        raise ValueError(f"Prompt must be at most {profile.prompt_character_limit} characters.")
    if not image_paths:
        raise ValueError("At least one input image is required.")
    if len(image_paths) > profile.max_input_images:
        raise ValueError(f"This model accepts at most {profile.max_input_images} input images.")
    total_pixels = 0
    for path in image_paths:
        info = validate_local_image(Path(path))
        total_pixels += info.width * info.height
    if total_pixels > MAX_TOTAL_PIXELS:
        raise ValueError("Total input images exceed the 33,177,600 pixel limit.")


def validate_local_image(path: Path) -> ImageInfo:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Image file does not exist: {path}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError("Images must be PNG, JPEG, WebP, or GIF files.")
    try:
        size = path.stat().st_size
        if size >= MAX_IMAGE_BYTES:
            raise ValueError("Each input image must be smaller than 25 MB.")
        with path.open("rb") as stream:
            header = stream.read(32)
            width, height, image_format = _read_dimensions(stream, header)
    except OSError as exc:
        raise ValueError(f"Unable to read image file: {path.name}") from exc
    if width < MIN_IMAGE_DIMENSION or height < MIN_IMAGE_DIMENSION:
        raise ValueError("Image dimensions must be at least 256 x 256 pixels.")
    if width * height > MAX_TOTAL_PIXELS:
        raise ValueError("Image dimensions exceed the 33,177,600 pixel limit.")
    return ImageInfo(width=width, height=height, format=image_format, size_bytes=size)


def _read_dimensions(stream, header: bytes) -> tuple[int, int, str]:
    if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
        return struct.unpack(">II", header[16:24]) + ("png",)
    if header.startswith((b"GIF87a", b"GIF89a")) and len(header) >= 10:
        return struct.unpack("<HH", header[6:10]) + ("gif",)
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return _webp_dimensions(stream, header)
    if header.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(stream, header)
    raise ValueError("Image header is malformed or its dimensions are unreadable.")


def _webp_dimensions(stream, header: bytes) -> tuple[int, int, str]:
    chunk = header[12:16]
    if chunk == b"VP8X" and len(header) >= 30:
        width = int.from_bytes(header[24:27], "little") + 1
        height = int.from_bytes(header[27:30], "little") + 1
        return width, height, "webp"
    if chunk == b"VP8 " and len(header) >= 30 and header[23:26] == b"\x9d\x01\x2a":
        width = int.from_bytes(header[26:28], "little") & 0x3FFF
        height = int.from_bytes(header[28:30], "little") & 0x3FFF
        return width, height, "webp"
    if chunk == b"VP8L" and len(header) >= 25 and header[20] == 0x2F:
        b1, b2, b3, b4 = header[21:25]
        width = 1 + b1 + ((b2 & 0x3F) << 8)
        height = 1 + (b2 >> 6) + (b3 << 2) + ((b4 & 0x0F) << 10)
        return width, height, "webp"
    raise ValueError("Image header is malformed or its dimensions are unreadable.")


def _jpeg_dimensions(stream, header: bytes) -> tuple[int, int, str]:
    data = bytearray(header[2:])
    while True:
        while data and data[0] != 0xFF:
            del data[0]
        while len(data) >= 2 and data[1] == 0xFF:
            del data[0]
        if len(data) < 4:
            data.extend(stream.read(4096))
            if len(data) < 4:
                raise ValueError("Image header is malformed or its dimensions are unreadable.")
            continue
        marker = data[1]
        del data[:2]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            continue
        while len(data) < 2:
            data.extend(stream.read(4096))
        segment_length = int.from_bytes(data[:2], "big")
        if segment_length < 2:
            raise ValueError("Image header is malformed or its dimensions are unreadable.")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            while len(data) < 7:
                data.extend(stream.read(4096))
            height = int.from_bytes(data[3:5], "big")
            width = int.from_bytes(data[5:7], "big")
            return width, height, "jpeg"
        del data[:segment_length]
        if len(data) < 4:
            data.extend(stream.read(4096))
