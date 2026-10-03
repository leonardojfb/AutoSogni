"""Upload local references to a configured HTTPS file service."""
from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class HTTPSUploader:
    def __init__(self, endpoint: str, token: str, *, asset_uploader=None, client=None):
        self.endpoint = endpoint.strip().rstrip("/")
        self.token = token.strip()
        self.asset_uploader = asset_uploader
        self.client = client or httpx.Client(timeout=300)

    def upload(self, path: Path, kind: str, group_id="") -> str:
        endpoint = urlsplit(self.endpoint)
        if endpoint.scheme != "https" or not endpoint.netloc:
            raise ValueError("Configurá la URL HTTPS pública del servicio de carga.")
        if not self.token:
            raise ValueError("Configurá el token del servicio HTTPS de carga.")
        if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError("La URL del servicio debe ser una URL base, sin credenciales ni parámetros.")

        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as source:
            response = self.client.post(
                f"{self.endpoint}/upload",
                headers={"Authorization": f"Bearer {self.token}"},
                files={"file": (path.name, source, media_type)},
                timeout=300,
            )
        if response.is_error:
            detail = response.text[:300]
            raise RuntimeError(f"El servicio HTTPS rechazó la carga (HTTP {response.status_code}): {detail}")
        try:
            public_url = response.json().get("url", "")
        except (ValueError, AttributeError) as exc:
            raise RuntimeError("El servicio HTTPS no devolvió JSON con una URL pública.") from exc
        parsed_url = urlsplit(public_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise RuntimeError("El servicio HTTPS devolvió una URL pública inválida.")
        if group_id:
            if self.asset_uploader is None:
                raise ValueError("Configurá AK y SK para registrar el archivo en la biblioteca de assets.")
            return self.asset_uploader.register_asset(path, kind, group_id, public_url)
        return public_url