from __future__ import annotations

import time
import json
import re
from pathlib import Path
from typing import Any, Callable

import httpx

from app.byteplus.schemas import BytePlusTask
from app.byteplus.validation import build_task_payload
from app.byteplus.media import prepare_snapshot


class BytePlusApiError(RuntimeError):
    def __init__(self, message: str, *, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class BytePlusClient:
    BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
    TERMINAL = frozenset({"succeeded", "failed", "cancelled", "expired"})
    def __init__(self, api_key: str = "", timeout: float = 60, client: httpx.Client | None = None, uploader=None) -> None:
        self.api_key, self._client = api_key.strip(), client or httpx.Client(timeout=timeout)
        self.uploader = uploader
    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"} if self.api_key else {"Content-Type": "application/json"}
    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        response = self._client.request(method, self.BASE_URL + path, headers=self._headers(), **kwargs)
        try: data = response.json()
        except ValueError as exc: raise BytePlusApiError(f"BytePlus returned non-JSON HTTP {response.status_code}.") from exc
        if response.is_error or (isinstance(data, dict) and data.get("error")):
            error = data.get("error") if isinstance(data, dict) else None
            if isinstance(error, dict):
                code = str(error.get("code") or "").strip()
                message = str(error.get("message") or "BytePlus request failed.")
                detail = f"{code}: {message}" if code else message
                raise BytePlusApiError(detail, code=code)
            raise BytePlusApiError(str(error or "BytePlus request failed."))
        if not isinstance(data, dict): raise BytePlusApiError("BytePlus returned an unexpected response.")
        return data
    def submit(self, snapshot: dict[str, Any]) -> BytePlusTask:
        build_task_payload(prepare_snapshot(snapshot, validate_only=True))
        payload = build_task_payload(prepare_snapshot(snapshot, self.uploader))
        if len(json.dumps(payload).encode("utf-8")) > 64 * 1024 * 1024:
            raise ValueError("Las referencias superan 64 MB por solicitud. Usá URLs o assets para esos archivos.")
        task = BytePlusTask.from_payload(self._request("POST", "/contents/generations/tasks", json=payload))
        if not task.id: raise BytePlusApiError("BytePlus submission did not return a task ID.")
        return task
    def get_task(self, task_id: str) -> BytePlusTask: return BytePlusTask.from_payload(self._request("GET", f"/contents/generations/tasks/{task_id}"))
    def test_connection(self) -> bool:
        if not self.api_key:
            raise BytePlusApiError("Ingresá una API key de BytePlus.")
        self._request("GET", "/contents/generations/tasks")
        return True
    def poll_task(self, task_id: str, *, sleep: Callable[[float], None] = time.sleep, timeout: float = 3600, on_update=None, cancel_event=None) -> BytePlusTask:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set(): raise BytePlusApiError("Polling cancelled locally.")
            task = self.get_task(task_id)
            if on_update: on_update(task)
            if task.status.lower() in self.TERMINAL: return task
            sleep(5)
        raise TimeoutError(f"Timed out waiting for BytePlus task {task_id}.")
    def generate_and_download(self, snapshot: dict[str, Any], output_dir: Path) -> Path:
        submitted = self.submit(snapshot)
        task = self.poll_task(submitted.id)
        if task.status.lower() != "succeeded":
            task_error = f"{task.error_code}: {task.error}" if task.error_code else task.error
            detail = f": {task_error}" if task_error else ""
            raise BytePlusApiError(f"BytePlus task {task.id} ended with status {task.status}{detail}")
        if not task.video_url:
            raise BytePlusApiError(f"BytePlus task {task.id} succeeded without a video URL.")
        safe_task_id = re.sub(r"[^A-Za-z0-9_.-]", "_", task.id)
        destination = output_dir / f"byteplus_{safe_task_id or 'video'}.mp4"
        return self.download_output(task.video_url, destination)
    def cancel_task(self, task_id: str) -> dict[str, Any]: return self._request("POST", f"/contents/generations/tasks/{task_id}/cancel")
    def delete_task(self, task_id: str) -> dict[str, Any]: return self._request("DELETE", f"/contents/generations/tasks/{task_id}")
    def download_output(self, url: str, destination: Path) -> Path:
        response = self._client.get(url, timeout=300)
        if response.is_error: raise BytePlusApiError(f"BytePlus output download failed (HTTP {response.status_code}).")
        destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(response.content); return destination
