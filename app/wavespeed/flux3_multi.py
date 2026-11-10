from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.wavespeed.validation import (
    FLUX_3_KLEIN_BASE_EDIT_MODEL_ID, TERMINAL_STATUSES,
    build_flux_3_klein_base_edit_payload, validate_local_reference_file,
)


_PAYLOAD_FIELDS = ("prompt", "images", "aspect_ratio", "resolution", "output_format",
                   "seed", "enable_sync_mode", "enable_base64_output")


def _payload(job: dict[str, Any], images: list[str]) -> dict[str, Any]:
    values = {name: job[name] for name in _PAYLOAD_FIELDS if name in job}
    values["images"] = images
    return build_flux_3_klein_base_edit_payload(**values)


def prepare_flux3_multi_jobs(*, sources: list[str], face: str, body: str,
                             prompt: str, settings: dict[str, Any],
                             output_dir: str) -> list[dict[str, Any]]:
    if not sources:
        raise ValueError("Seleccioná al menos una imagen para Ref 1.")
    if not face or not body:
        raise ValueError("Seleccioná la cara (Ref 2) y el cuerpo (Ref 3).")
    source_paths = [str(Path(name).resolve()) for name in sources]
    face_path, body_path = str(Path(face).resolve()), str(Path(body).resolve())
    for name in source_paths + [face_path, body_path]:
        validate_local_reference_file(Path(name), "reference_images")
    jobs = []
    for source in source_paths:
        job = {"source": source, "images": [source, face_path, body_path],
               "prompt": prompt, **settings, "output_dir": output_dir,
               "status": "pending", "task_id": "", "output_file": "", "error": "",
               "created_at": datetime.now(timezone.utc).isoformat()}
        _payload(job, job["images"])
        jobs.append(job)
    return jobs


def run_flux3_multi_jobs(jobs: list[dict[str, Any]], client, *,
                         upload_file: Callable[[Path], dict[str, Any]],
                         save_output: Callable[[object, dict[str, Any], str, str], str],
                         on_update: Callable[[dict[str, Any]], None] | None = None) -> list[dict[str, Any]]:
    uploads: dict[str, str] = {}

    def notify(job: dict[str, Any]) -> None:
        if on_update:
            on_update(dict(job))

    for job in jobs:
        if job["status"] not in {"pending", "paused"}:
            continue
        job["status"] = "running"
        job["error"] = ""
        notify(job)
        try:
            if job.get("task_id"):
                prediction = client.get_result(job["task_id"])
            else:
                urls = []
                for name in job["images"]:
                    path = Path(name)
                    validate_local_reference_file(path, "reference_images")
                    if name not in uploads:
                        uploads[name] = upload_file(path)["download_url"]
                    urls.append(uploads[name])
                payload = _payload(job, urls)
                job["payload"] = payload
                prediction = client.submit(payload, model_id=FLUX_3_KLEIN_BASE_EDIT_MODEL_ID)
                job["task_id"] = prediction.id
                notify(job)
            if prediction.status not in TERMINAL_STATUSES:
                prediction = client.poll_result(prediction.id)
            if prediction.status == "completed":
                if not prediction.outputs:
                    raise ValueError("FLUX 3 terminó sin imagen de salida.")
                job["output_file"] = save_output(
                    prediction.outputs[0], job, prediction.id,
                    f".{job.get('output_format', 'jpeg')}",
                )
                job["status"] = "completed"
            else:
                job["status"] = "failed"
                job["error"] = prediction.error or f"WaveSpeed: {prediction.status}"
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc)
        notify(job)
    return jobs
