from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from app.wavespeed.schemas import WaveSpeedPrediction
from app.wavespeed.validation import MODEL_ID, TERMINAL_STATUSES, validate_request


class WaveSpeedApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, error_code: Any = None) -> None:
        self.status_code = status_code
        self.error_code = error_code
        super().__init__(message)


class WaveSpeedClient:
    BASE_URL = "https://api.wavespeed.ai/api/v3"
    MODEL_ID = MODEL_ID
    MAX_UPLOAD_BYTES = 209_715_200

    def __init__(self, api_key: str = "", timeout: float = 60.0, client: httpx.Client | None = None) -> None:
        self.api_key = api_key.strip()
        self._client = client or httpx.Client(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.BASE_URL}{path}"

    @staticmethod
    def _unwrap(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise WaveSpeedApiError(
                f"WaveSpeed returned a non-JSON response (HTTP {response.status_code}).",
                status_code=response.status_code,
            ) from exc
        data = body.get("data", body) if isinstance(body, dict) else body
        if response.is_error or (isinstance(body, dict) and body.get("code") not in (None, 200)):
            message = "WaveSpeed request failed."
            if isinstance(body, dict):
                message = str(body.get("message") or body.get("error") or message)
            raise WaveSpeedApiError(message, status_code=response.status_code, error_code=body.get("code") if isinstance(body, dict) else None)
        if not isinstance(data, dict):
            raise WaveSpeedApiError("WaveSpeed returned an unexpected response shape.", status_code=response.status_code)
        return data

    def upload_file(self, path: Path) -> dict[str, Any]:
        path = Path(path)
        if not path.is_file():
            raise ValueError(f"Reference file does not exist: {path}")
        size = path.stat().st_size
        if size > self.MAX_UPLOAD_BYTES:
            raise ValueError(f"Reference file exceeds the 200 MB upload limit: {path.name}")
        payload: dict[str, Any] = {"filename": path.name, "size": size}
        content_type = mimetypes.guess_type(path.name)[0]
        if content_type:
            payload["content_type"] = content_type
        ticket = self._unwrap(self._client.post(self._url("/media/uploads"), headers=self._headers(), json=payload))
        upload = ticket.get("upload") or {}
        upload_url = upload.get("url")
        download_url = ticket.get("download_url")
        if not upload_url or not download_url:
            raise WaveSpeedApiError("WaveSpeed upload ticket did not include upload and download URLs.")
        with path.open("rb") as handle:
            upload_response = self._client.put(upload_url, headers=dict(upload.get("headers") or {}), content=handle)
        if upload_response.is_error:
            raise WaveSpeedApiError(
                f"WaveSpeed storage upload failed (HTTP {upload_response.status_code}).",
                status_code=upload_response.status_code,
            )
        return {
            "download_url": str(download_url),
            "filename": path.name,
            "size": size,
            "content_type": content_type or "application/octet-stream",
        }

    def submit(self, payload: dict[str, Any], webhook_url: str | None = None) -> WaveSpeedPrediction:
        validate_request(payload, webhook_url=webhook_url)
        params = {"webhook": webhook_url} if webhook_url else None
        data = self._unwrap(
            self._client.post(
                self._url(f"/{self.MODEL_ID}"),
                headers=self._headers(),
                params=params,
                json=payload,
            )
        )
        prediction = WaveSpeedPrediction.from_payload(data)
        if not prediction.id:
            raise WaveSpeedApiError("WaveSpeed submission did not return a prediction ID.")
        return prediction

    def get_result(self, task_id: str) -> WaveSpeedPrediction:
        data = self._unwrap(self._client.get(self._url(f"/predictions/{task_id}/result"), headers=self._headers()))
        return WaveSpeedPrediction.from_payload(data)

    def poll_result(
        self,
        task_id: str,
        *,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 3600.0,
        on_update: Callable[[WaveSpeedPrediction], None] | None = None,
        cancel_event: Any = None,
    ) -> WaveSpeedPrediction:
        deadline = time.monotonic() + timeout
        interval = 2.0
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise WaveSpeedApiError("Polling cancelled locally.")
            result = self.get_result(task_id)
            if on_update:
                on_update(result)
            if result.status in TERMINAL_STATUSES:
                return result
            sleep(interval)
            interval = min(10.0, interval + 1.0)
        raise TimeoutError(f"Timed out waiting for WaveSpeed prediction {task_id}.")

    def get_balance(self) -> float:
        data = self._unwrap(self._client.get(self._url("/balance"), headers=self._headers()))
        return float(data.get("balance", 0))

    def estimate_price(self, payload: dict[str, Any]) -> dict[str, Any]:
        validate_request(payload)
        return self._unwrap(
            self._client.post(
                self._url("/model/price"),
                headers=self._headers(),
                json={"model_id": self.MODEL_ID, "inputs": payload},
            )
        )

    def list_predictions(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        return self._unwrap(
            self._client.post(
                self._url("/predictions"),
                headers=self._headers(),
                json={"page": page, "page_size": page_size},
            )
        )

    def delete_tasks(self, task_ids: list[str]) -> int:
        data = self._unwrap(
            self._client.post(
                self._url("/predictions/delete"),
                headers=self._headers(),
                json={"ids": list(task_ids)},
            )
        )
        return int(data.get("deleted_count", 0))

    def download_output(self, url: str, destination: Path) -> Path:
        response = self._client.get(url, timeout=300.0)
        if response.is_error:
            raise WaveSpeedApiError(
                f"WaveSpeed output download failed (HTTP {response.status_code}).",
                status_code=response.status_code,
            )
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination
