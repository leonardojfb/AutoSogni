"""Synchronous, HTTP-only Venice image edit client."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.venice.validation import ModelProfile, parse_model_catalog


@dataclass(frozen=True)
class VeniceImageResult:
    content: bytes
    content_type: str
    request_id: str
    headers: dict[str, str]


class VeniceApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, provider_error: str = "") -> None:
        self.status_code = status_code
        self.provider_error = provider_error
        super().__init__(message)


class VeniceClient:
    BASE_URL = "https://api.venice.ai"
    IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}

    def __init__(self, api_key: str = "", timeout: float = 120.0, client: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _request(self, method: str, path: str, *, payload: dict[str, object] | None = None) -> httpx.Response:
        try:
            response = self._client.request(
                method, f"{self.BASE_URL}{path}", headers=self._headers(),
                **({"json": payload} if payload is not None else {}),
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise VeniceApiError("Venice request timed out.") from exc
        except httpx.RequestError as exc:
            raise VeniceApiError("Venice network request failed.") from exc
        if response.is_error:
            provider_error = self._provider_error(response, payload)
            detail = f" ({provider_error})" if provider_error else ""
            raise VeniceApiError(
                f"Venice API returned HTTP {response.status_code}{detail}.",
                status_code=response.status_code, provider_error=provider_error,
            )
        return response

    def _provider_error(self, response: httpx.Response, payload: dict[str, object] | None) -> str:
        try:
            body = response.json()
        except (ValueError, UnicodeError):
            return ""
        if not isinstance(body, dict):
            return ""
        error = body.get("error")
        if isinstance(error, dict):
            value = error.get("message") or error.get("code")
        else:
            value = error or body.get("message")
        if not isinstance(value, str):
            return ""
        message = value.replace(self.api_key, "[redacted]") if self.api_key else value
        # A provider may echo input data in its error message. Never put those values in exceptions.
        def redact_values(item: object) -> None:
            nonlocal message
            if isinstance(item, str) and item:
                message = message.replace(item, "[redacted]")
            elif isinstance(item, dict):
                for child in item.values():
                    redact_values(child)
            elif isinstance(item, list):
                for child in item:
                    redact_values(child)

        if payload is not None:
            redact_values(payload)
        return " ".join(message.split())[:200]

    def list_edit_models(self) -> dict[str, ModelProfile]:
        response = self._request("GET", "/api/v1/models?type=inpaint")
        try:
            data = response.json()
        except (ValueError, UnicodeError) as exc:
            raise VeniceApiError("Venice model catalog returned invalid JSON.", response.status_code) from exc
        return parse_model_catalog(data)

    def multi_edit(self, payload: dict[str, object]) -> VeniceImageResult:
        response = self._request("POST", "/api/v1/image/multi-edit", payload=payload)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in self.IMAGE_TYPES:
            raise VeniceApiError("Venice returned an unsupported image content type.", response.status_code)
        return VeniceImageResult(
            content=response.content, content_type=content_type,
            request_id=self._redact(response.headers.get("x-request-id", "")),
            headers={key: self._redact(value) for key, value in response.headers.items()},
        )

    def _redact(self, value: str) -> str:
        return value.replace(self.api_key, "[redacted]") if self.api_key else value
