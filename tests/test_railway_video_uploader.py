from __future__ import annotations

from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from railway_video_uploader.server import app


def test_upload_returns_signed_https_url_and_file_is_fetchable(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("UPLOAD_TOKEN", "secret-test-token")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://upload.example")
    client = TestClient(app)

    response = client.post(
        "/upload",
        headers={"Authorization": "Bearer secret-test-token"},
        files={"file": ("clip.mp4", b"video-data", "video/mp4")},
    )

    assert response.status_code == 200
    upload = response.json()
    assert urlsplit(upload["url"]).scheme == "https"
    parsed = urlsplit(upload["url"])
    download = client.get(parsed.path + "?" + parsed.query)
    assert download.status_code == 200
    assert download.content == b"video-data"


def test_upload_requires_bearer_token(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("UPLOAD_TOKEN", "secret-test-token")
    client = TestClient(app)

    response = client.post("/upload", files={"file": ("clip.mp4", b"video", "video/mp4")})

    assert response.status_code == 401
    assert not list(tmp_path.iterdir())


def test_download_rejects_invalid_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("UPLOAD_TOKEN", "secret-test-token")
    client = TestClient(app)

    response = client.get("/files/0123456789abcdef0123456789abcdef.mp4?expires=9999999999&signature=bad")

    assert response.status_code == 403