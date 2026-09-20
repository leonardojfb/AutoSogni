from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from app.core.validators import validate_minimax_h3_prompt
from app.sogni.schemas import ModelDescriptor

log = logging.getLogger(__name__)


def _detect_image_content_type(image_path: Path) -> str:
    with image_path.open("rb") as fh:
        header = fh.read(32)
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"


class SogniRateLimitError(RuntimeError):
    def __init__(self, retry_after: int | None = None) -> None:
        self.retry_after = retry_after
        wait = f" Retry after {retry_after} seconds." if retry_after is not None else ""
        super().__init__(f"Sogni workflow start rate limit exceeded.{wait}")


class SogniClient:
    BASE_URL = "https://api.sogni.ai"

    WORKFLOW_MODEL_ALIASES = {
        "minimax-h3-fl2va-fp8_i2v": "minimax-h3-i2v",
        "minimax-h3-fl2va-fp8_i2v_balanced": "minimax-h3-i2v-balanced",
        "minimax-h3-fl2va-fp8_i2v_turbo": "minimax-h3-i2v-turbo",
    }
    EXTERNAL_REFERENCE_MODEL_PREFIXES = ("seedance", "happyhorse", "wan3", "wan-3")

    def __init__(self, api_key: str = "", timeout: float = 60.0, client: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self._client = client or httpx.Client(base_url=self.BASE_URL, timeout=timeout)

    @staticmethod
    def _extract_workflow_payload(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data") or {}
        if isinstance(data, dict):
            workflow = data.get("workflow")
            if isinstance(workflow, dict):
                return workflow
            if data.get("id") or data.get("status") or data.get("steps") or data.get("artifacts"):
                return data
        return {}

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def test_connection(self) -> tuple[bool, str]:
        try:
            self.fetch_video_models()
            if self.api_key:
                response = self._client.get("/v4/account/balance", headers=self._headers())
                if response.status_code == 401:
                    return False, "Authentication failed"
            return True, "Connected"
        except httpx.HTTPError as exc:
            return False, f"Network unavailable: {exc}"

    def fetch_video_models(self) -> list[ModelDescriptor]:
        response = self._client.get("/v1/model-catalog", params={"mediaType": "video", "include": "parameters"})
        response.raise_for_status()
        data = response.json().get("data", {})
        return [ModelDescriptor.from_api(item) for item in data.get("models", [])]

    def fetch_model(self, model_id: str) -> ModelDescriptor:
        response = self._client.get(f"/v1/model-catalog/{model_id}", params={"include": "parameters"})
        response.raise_for_status()
        return ModelDescriptor.from_api(response.json().get("data", {}).get("model", {}))

    def start_image_to_video_workflow(
        self,
        title: str,
        prompt: str,
        model_id: str,
        settings: dict[str, Any],
        media_reference: dict[str, Any] | None,
        idempotency_key: str,
        skip_prompt_processing: bool = True,
    ) -> dict[str, Any]:
        validate_minimax_h3_prompt(prompt, model_id)
        payload = self.build_image_to_video_payload(
            title,
            prompt,
            model_id,
            settings,
            media_reference,
            skip_prompt_processing=skip_prompt_processing,
        )
        log.info("Posting workflow payload: %s", json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        response = self._client.post("/v1/creative-agent/workflows", headers=self._headers(idempotency_key), json=payload)
        if response.is_error:
            try:
                body = response.json()
            except Exception:
                body = response.text
            log.error(
                "Sogni workflow rejected: status=%s body=%s payload=%s",
                response.status_code,
                json.dumps(body, ensure_ascii=False, indent=2, default=str) if isinstance(body, dict) else body,
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            )
            if response.status_code == 429:
                retry_after = None
                if isinstance(body, dict):
                    details = body.get("details") or {}
                    retry_after = body.get("retryAfter") or details.get("retryAfterSeconds")
                if retry_after is None:
                    retry_after = response.headers.get("Retry-After")
                try:
                    retry_after = int(retry_after) if retry_after is not None else None
                except (TypeError, ValueError):
                    retry_after = None
                raise SogniRateLimitError(retry_after)
        response.raise_for_status()
        return self._extract_workflow_payload(response.json())

    def read_workflow(self, workflow_id: str) -> dict[str, Any]:
        response = self._client.get(f"/v1/creative-agent/workflows/{workflow_id}", headers=self._headers())
        response.raise_for_status()
        return self._extract_workflow_payload(response.json())

    def resume_workflow(self, workflow_id: str) -> dict[str, Any]:
        response = self._client.post(
            f"/v1/creative-agent/workflows/{workflow_id}/resume",
            headers=self._headers(),
            json={"token_type": "auto", "app_source": "autosogni-local"},
        )
        response.raise_for_status()
        return self._extract_workflow_payload(response.json())

    def get_image_upload_url(self, job_id: str, image_path: Path) -> dict[str, Any]:
        content_type = _detect_image_content_type(image_path)
        response = self._client.get(
            "/v2/image/uploadUrl",
            headers=self._headers(),
            params={"type": "startingImage", "jobId": job_id, "contentType": content_type},
        )
        response.raise_for_status()
        upload = response.json().get("data", {})
        upload["_job_id"] = job_id
        upload["_type"] = "startingImage"
        upload["_content_type"] = content_type
        return upload

    def upload_image_to_presigned_post(self, upload: dict[str, Any], image_path: Path) -> dict[str, Any]:
        fields = upload["fields"]
        with image_path.open("rb") as fh:
            response = httpx.post(upload["url"], data=fields, files={"file": (image_path.name, fh, fields.get("Content-Type"))})
        response.raise_for_status()
        content_type = fields.get("Content-Type") or upload.get("_content_type") or "image/png"
        download_response = self._client.get(
            "/v2/image/downloadUrl",
            headers=self._headers(),
            params={
                "type": upload.get("_type", "startingImage"),
                "jobId": upload["_job_id"],
                "contentType": content_type,
            },
        )
        download_response.raise_for_status()
        download_url = download_response.json().get("data", {}).get("downloadUrl")
        if not download_url:
            raise RuntimeError("Sogni image upload completed but no signed download URL was returned.")
        return {"kind": "image", "url": download_url, "contentType": content_type}

    @staticmethod
    def _normalize_media_reference(media_reference: dict[str, Any] | None) -> dict[str, Any] | None:
        if not media_reference:
            return None

        if isinstance(media_reference, dict) and media_reference.get("url"):
            kind = media_reference.get("kind") or media_reference.get("type")
            if kind == "startingImage":
                kind = "image"
            reference = {"kind": kind or "image", "url": media_reference["url"]}
            if media_reference.get("contentType"):
                reference["contentType"] = media_reference["contentType"]
            return reference

        if isinstance(media_reference, dict) and media_reference.get("key"):
            key = media_reference["key"]
            if key.startswith("http://") or key.startswith("https://"):
                url = key
            else:
                url = f"https://artist-upload-production.s3-accelerate.amazonaws.com/{key.lstrip('/')}"
            kind = media_reference.get("kind") or media_reference.get("type")
            if kind == "startingImage":
                kind = "image"
            reference = {"kind": kind or "image", "url": url}
            if media_reference.get("contentType"):
                reference["contentType"] = media_reference["contentType"]
            return reference

        raise ValueError(
            "Invalid media reference supplied to Sogni workflow payload. Expected a URL-based request media object or uploaded key metadata."
        )

    @staticmethod
    def build_image_to_video_payload(
        title: str,
        prompt: str,
        model_id: str,
        settings: dict[str, Any],
        media_reference: dict[str, Any] | None,
        skip_prompt_processing: bool = True,
    ) -> dict[str, Any]:
        workflow_model_id = SogniClient.WORKFLOW_MODEL_ALIASES.get(model_id, model_id)
        arguments: dict[str, Any] = {"prompt": prompt, "videoModel": workflow_model_id}
        arguments.update({key: value for key, value in settings.items() if value not in ("", None)})

        normalized_reference = SogniClient._normalize_media_reference(media_reference)
        tool_name = "generate_video"
        if model_id.startswith("minimax-h3-fl2va-fp8_i2v") or workflow_model_id.startswith("minimax-h3-i2v"):
            tool_name = "animate_photo"
            arguments["sourceImageIndex"] = -1
            arguments["generateAudio"] = True
            if skip_prompt_processing:
                arguments["skipPromptProcessing"] = True
        elif normalized_reference:
            if not SogniClient._supports_external_reference_url(workflow_model_id):
                raise ValueError(
                    "External reference URLs are supported only by Seedance, HappyHorse, and Wan 3 models. "
                    f"Selected model '{workflow_model_id}' cannot be used with uploaded image references."
                )
            arguments["referenceImageIndices"] = [-1]

        step: dict[str, Any] = {"id": "clip", "toolName": tool_name, "arguments": arguments}
        payload: dict[str, Any] = {
            "input": {"title": title, "steps": [step]},
            "token_type": "auto",
            "confirm_cost": True,
        }
        if normalized_reference:
            payload["media_references"] = [normalized_reference]
        return payload

    @staticmethod
    def _supports_external_reference_url(model_id: str) -> bool:
        normalized = model_id.lower().replace("_", "-")
        return normalized.startswith(SogniClient.EXTERNAL_REFERENCE_MODEL_PREFIXES)
