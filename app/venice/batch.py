"""Two-stage Venice visual reconstruction batch orchestration."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from uuid import uuid4

from app.venice.client import VeniceApiError, VeniceClient, VeniceImageResult
from app.venice.validation import (
    ModelProfile,
    build_multi_edit_payload,
    validate_local_image,
    validate_prompt_and_images,
)


EDIT_MODEL = "qwen-image-2-1-pro-edit"
DEFAULT_VISION_MODEL = "qwen3-vl-235b-a22b"
SUPPORTED_SOURCE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
}
_IMAGE_SUFFIX = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


@dataclass(frozen=True)
class BatchConfig:
    input_dir: Path
    output_parent: Path
    master_prompt: str
    face_path: Path
    body_path: Path
    vision_model: str = DEFAULT_VISION_MODEL


@dataclass(frozen=True)
class BatchEvent:
    index: int
    filename: str
    stage: str
    status: str
    output_path: Path | None = None
    prompt_path: Path | None = None
    error: str | None = None


def run_batch(
    client: VeniceClient,
    profile: ModelProfile,
    config: BatchConfig,
    progress=None,
    stop_event: threading.Event | None = None,
) -> Path:
    """Run the vision-then-edit flow once for every immediate source photo."""
    stop_event = stop_event or threading.Event()
    input_dir, output_parent, master_prompt, face_path, body_path, vision_model = _snapshot_config(config)
    sources = _preflight(input_dir, output_parent, master_prompt, face_path, body_path, profile, vision_model)
    face_bytes = face_path.read_bytes()
    body_bytes = body_path.read_bytes()
    if not face_bytes or not body_bytes:
        raise ValueError("Identity reference images must not be empty.")
    face_b64 = base64.b64encode(face_bytes).decode("ascii")
    body_b64 = base64.b64encode(body_bytes).decode("ascii")

    run_dir = _create_run_directory(output_parent)
    manifest_path = run_dir / "manifest.json"
    items = [
        {
            "source": source.name,
            "stage": "pending",
            "status": "pending",
            "prompt_file": None,
            "output_file": None,
            "error": None,
        }
        for source in sources
    ]
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "vision_model": vision_model,
        "edit_model": EDIT_MODEL,
        "edit_options": {
            "resolution": "2K", "aspect_ratio": "auto", "output_format": "jpeg", "safe_mode": False,
        },
        "master_prompt_sha256": hashlib.sha256(master_prompt.encode("utf-8")).hexdigest(),
        "references": [
            {"slot": 1, "filename": face_path.name},
            {"slot": 2, "filename": body_path.name},
        ],
        "items": items,
    }
    _write_manifest(manifest_path, manifest)
    known_paths = (input_dir, output_parent, face_path, body_path, run_dir)
    for index, source in enumerate(sources, start=1):
        _emit(progress, BatchEvent(index, source.name, "queued", "pending"))

    halted = False
    for index, source in enumerate(sources, start=1):
        if stop_event.is_set():
            halted = True
            break
        item = items[index - 1]
        prompt_path = run_dir / _prompt_filename(index, source)
        _set_item(item, stage="vision", status="processing", error=None)
        _write_manifest(manifest_path, manifest)
        _emit(progress, BatchEvent(index, source.name, "vision", "processing"))
        try:
            validate_local_image(source)
            source_bytes = source.read_bytes()
            if not source_bytes:
                raise ValueError("Source image is empty.")
            prompt = client.analyze_photo(
                model=vision_model,
                master_prompt=master_prompt,
                image_bytes=source_bytes,
                mime_type=_MIME_BY_SUFFIX[source.suffix.lower()],
            )
        except Exception as exc:
            error = _sanitize_error(exc, client, master_prompt, known_paths + (source,))
            _set_item(item, stage="vision", status="failed", error=error)
            _write_manifest(manifest_path, manifest)
            _emit(progress, BatchEvent(index, source.name, "vision", "failed", error=error))
            if _is_fatal(exc):
                halted = True
                break
            continue

        try:
            _atomic_write_text(prompt_path, prompt)
        except Exception as exc:
            error = _sanitize_error(exc, client, master_prompt, known_paths + (source, prompt_path))
            _set_item(item, stage="prompt", status="failed", error=error)
            _write_manifest(manifest_path, manifest)
            _emit(progress, BatchEvent(index, source.name, "prompt", "failed", error=error))
            continue
        item["prompt_file"] = prompt_path.name
        _set_item(item, stage="prompt", status="saved", error=None)
        _write_manifest(manifest_path, manifest)
        _emit(progress, BatchEvent(index, source.name, "prompt", "saved", prompt_path=prompt_path))

        try:
            payload = build_multi_edit_payload(
                profile=profile,
                prompt=prompt,
                images=[face_b64, body_b64],
                aspect_ratio="auto",
                resolution="2K",
                output_format="jpeg",
                safe_mode=False,
            )
        except Exception as exc:
            error = _sanitize_error(exc, client, master_prompt, known_paths + (source, prompt_path))
            _set_item(item, stage="prompt", status="failed", error=error)
            _write_manifest(manifest_path, manifest)
            _emit(progress, BatchEvent(index, source.name, "prompt", "failed", prompt_path=prompt_path, error=error))
            continue

        _set_item(item, stage="edit", status="processing", error=None)
        _write_manifest(manifest_path, manifest)
        _emit(progress, BatchEvent(index, source.name, "edit", "processing", prompt_path=prompt_path))
        output_path: Path | None = None
        try:
            result = client.multi_edit(payload)
            output_path = _output_path(run_dir, index, source, result)
            _atomic_write_bytes(output_path, result.content)
        except Exception as exc:
            extra_paths = (source, prompt_path) + ((output_path,) if output_path is not None else ())
            error = _sanitize_error(exc, client, master_prompt, known_paths + extra_paths)
            _set_item(item, stage="edit", status="failed", error=error)
            _write_manifest(manifest_path, manifest)
            _emit(progress, BatchEvent(index, source.name, "edit", "failed", prompt_path=prompt_path, error=error))
            if _is_fatal(exc):
                halted = True
                break
            continue

        item["output_file"] = output_path.name
        _set_item(item, stage="done", status="completed", error=None)
        _write_manifest(manifest_path, manifest)
        _emit(progress, BatchEvent(
            index, source.name, "done", "completed", output_path=output_path, prompt_path=prompt_path,
        ))

    if halted or stop_event.is_set():
        for index, item in enumerate(items, start=1):
            if item["status"] == "pending":
                _set_item(item, stage="stopped", status="stopped", error=None)
                _emit(progress, BatchEvent(index, item["source"], "stopped", "stopped"))
        _write_manifest(manifest_path, manifest)
    return run_dir


def _snapshot_config(config: BatchConfig) -> tuple[Path, Path, str, Path, Path, str]:
    return (
        Path(config.input_dir), Path(config.output_parent), str(config.master_prompt),
        Path(config.face_path), Path(config.body_path), str(config.vision_model),
    )


def _preflight(
    input_dir: Path, output_parent: Path, master_prompt: str,
    face_path: Path, body_path: Path, profile: ModelProfile, vision_model: str,
) -> list[Path]:
    if profile is None or profile.id != EDIT_MODEL:
        raise ValueError("Qwen Image 2.1 Pro Edit is unavailable")
    if "2K" not in profile.resolutions:
        raise ValueError("Qwen Image 2.1 Pro Edit does not support the required 2K resolution.")
    if "auto" not in profile.aspect_ratios:
        raise ValueError("Qwen Image 2.1 Pro Edit does not support the required auto aspect ratio.")
    if not vision_model.strip():
        raise ValueError("Vision model is required.")
    if not master_prompt.strip():
        raise ValueError("Vision master prompt is required.")
    if not input_dir.is_dir():
        raise ValueError("Source folder does not exist.")
    if input_dir.resolve() == output_parent.resolve():
        raise ValueError("Source folder and output parent must be different folders.")
    sources = sorted(
        (path for path in input_dir.iterdir()
         if path.is_file() and path.suffix.lower() in SUPPORTED_SOURCE_EXTENSIONS),
        key=lambda path: path.name.casefold(),
    )
    if not sources:
        raise ValueError("No supported photos found in the source folder.")
    try:
        validate_prompt_and_images(profile=profile, prompt="preflight", image_paths=[face_path, body_path])
    except Exception as exc:
        message = _sanitize_error(exc, object(), "", (input_dir, output_parent, face_path, body_path))
        raise ValueError(message) from None
    return sources


def _create_run_directory(output_parent: Path) -> Path:
    output_parent.mkdir(parents=True, exist_ok=True)
    for _ in range(10):
        candidate = output_parent / (
            f"venice_batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        )
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise OSError("Unable to create a unique Venice batch output folder.")


def _prompt_filename(index: int, source: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", source.stem).strip("._-")[:80] or "photo"
    return f"{index:04d}_{stem}.prompt.txt"


def _output_path(run_dir: Path, index: int, source: Path, result: VeniceImageResult) -> Path:
    suffix = _IMAGE_SUFFIX.get(result.content_type)
    if suffix is None:
        raise ValueError("Venice returned an unsupported image content type.")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", source.stem).strip("._-")[:80] or "photo"
    return run_dir / f"{index:04d}_{stem}{suffix}"


def _set_item(item: dict[str, object], *, stage: str, status: str, error: str | None) -> None:
    item["stage"] = stage
    item["status"] = status
    item["error"] = error


def _emit(progress, event: BatchEvent) -> None:
    if progress is not None:
        progress(event)


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    _atomic_write_text(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _is_fatal(error: BaseException) -> bool:
    return isinstance(error, VeniceApiError) and error.status_code in {401, 402, 429}


def _sanitize_error(error: BaseException, client: object, master_prompt: str, known_paths: tuple[Path, ...]) -> str:
    text = " ".join(str(error).split())
    api_key = getattr(client, "api_key", "")
    if api_key:
        text = text.replace(str(api_key), "[redacted]")
    if master_prompt:
        text = text.replace(master_prompt, "[redacted]")
    for path in sorted(known_paths, key=lambda value: len(str(value)), reverse=True):
        text = text.replace(str(path), path.name)
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", text)
    text = re.sub(r"(?i)\b(?:secret|private)[-_][a-z0-9_-]+\b", "[redacted]", text)
    text = re.sub(r"\b[A-Za-z0-9+/]{24,}={0,2}\b", "[redacted]", text)
    return text[:240] or "Venice batch item failed."
