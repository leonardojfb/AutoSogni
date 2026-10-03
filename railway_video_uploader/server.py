from __future__ import annotations

import hashlib
import hmac
import mimetypes
import os
import re
import time
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse


MAX_UPLOAD_BYTES = 200 * 1024 * 1024 - 1
URL_TTL_SECONDS = 24 * 60 * 60
RETENTION_SECONDS = 7 * 24 * 60 * 60
ALLOWED_SUFFIXES = {".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp", ".mp3", ".wav"}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _upload_dir() -> Path:
    return Path(os.environ.get("UPLOAD_DIR", "/data"))


def _public_base_url() -> str:
    base_url = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        base_url = f"https://{domain}" if domain else ""
    if not base_url.startswith("https://"):
        raise HTTPException(status_code=503, detail="HTTPS public domain is not configured.")
    return base_url


def _upload_token() -> str:
    return os.environ.get("UPLOAD_TOKEN", "").strip()


def _signature(filename: str, expires: int, token: str) -> str:
    message = f"{filename}:{expires}".encode("ascii")
    return hmac.new(token.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _remove_expired_files(directory: Path) -> None:
    cutoff = time.time() - RETENTION_SECONDS
    for path in directory.iterdir():
        try:
            if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/upload")
async def upload(file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    token = _upload_token()
    if not token:
        raise HTTPException(status_code=503, detail="Upload service is not configured.")
    expected = f"Bearer {token}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid upload credentials.")
    base_url = _public_base_url()

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=415, detail="Unsupported media format.")

    directory = _upload_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _remove_expired_files(directory)
    filename = f"{uuid4().hex}{suffix}"
    destination = directory / filename
    temporary = directory / f"{filename}.part"
    size = 0
    try:
        with temporary.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Media file exceeds the upload limit.")
                output.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Media file is empty.")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    expires = int(time.time()) + URL_TTL_SECONDS
    signature = _signature(filename, expires, token)
    url = f"{base_url}/files/{filename}?expires={expires}&signature={signature}"
    return {"url": url, "expires_at": expires}


@app.get("/files/{filename}")
def download(filename: str, expires: int, signature: str):
    if not re.fullmatch(r"[a-f0-9]{32}\.(mp4|mov|jpg|jpeg|png|webp|mp3|wav)", filename):
        raise HTTPException(status_code=404, detail="File not found.")
    token = _upload_token()
    now = int(time.time())
    if not token or expires < now or expires > now + URL_TTL_SECONDS + 60:
        raise HTTPException(status_code=403, detail="Download URL expired or invalid.")
    if not hmac.compare_digest(signature, _signature(filename, expires, token)):
        raise HTTPException(status_code=403, detail="Download URL expired or invalid.")
    path = _upload_dir() / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        headers={"Cache-Control": "private, no-store"})