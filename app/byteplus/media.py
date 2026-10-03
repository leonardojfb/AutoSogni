"""Local references stay local until submission; no credentials in snapshots."""
from __future__ import annotations

import base64
import copy
import mimetypes
from pathlib import Path

FORMATS = {"image": {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif", ".heic", ".heif"},
           "video": {".mp4", ".mov"}, "audio": {".mp3", ".wav"}}
LIMITS = {"image": 30, "video": 200, "audio": 15}
FIELDS = {"first_frame_url": "image", "last_frame_url": "image", "reference_video_url": "video",
          "reference_images": "image", "reference_videos": "video", "reference_audios": "audio"}


def validate_file(value, kind):
    path = Path(value)
    if not path.is_file():
        raise ValueError(f"No existe el archivo: {path}")
    if path.suffix.lower() not in FORMATS[kind]:
        raise ValueError(f"Formato no compatible para {kind}: {path.name}")
    if not 0 < path.stat().st_size < LIMITS[kind] * 1024 * 1024:
        raise ValueError(f"{path.name}: archivo vacío o supera {LIMITS[kind]} MB.")
    return path


def prepare_snapshot(snapshot, uploader=None, *, validate_only=False):
    result = copy.deepcopy(snapshot)
    for field, kind in FIELDS.items():
        if field not in result:
            continue
        is_list = isinstance(result[field], list)
        values = result[field] if is_list else [result[field]]
        converted = []
        for value in values:
            if str(value).startswith(("https://", "http://", "asset://", "data:")):
                converted.append(value)
                continue
            path = validate_file(value, kind)
            if validate_only:
                converted.append("https://local-validation.invalid/" + path.name)
            elif uploader and (kind == "video" or snapshot.get("asset_group_id")):
                converted.append(uploader.upload(path, kind, snapshot.get("asset_group_id", "")))
            elif kind == "video":
                raise ValueError("Para subir videos locales, configurá TOS o un servicio HTTPS en Carga de referencias.")
            else:
                mime = mimetypes.guess_type(path.name)[0] or f"{kind}/{path.suffix[1:]}"
                converted.append(f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii"))
        result[field] = converted if is_list else converted[0]
    return result
