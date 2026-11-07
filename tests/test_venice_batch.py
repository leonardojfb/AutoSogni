"""Focused tests for the Venice visual reconstruction batch runner."""

from __future__ import annotations

import base64
from dataclasses import replace
import json
from io import BytesIO
from pathlib import Path
import threading

import httpx
import pytest
from PIL import Image

import app.venice.batch as batch_module
from app.venice.batch import BatchConfig, DEFAULT_VISION_MODEL, run_batch
from app.venice.client import VeniceApiError, VeniceClient, VeniceImageResult
from app.venice.validation import ModelProfile


QWEN = "qwen-image-2-1-pro-edit"
def _profile(limit=10000):
    return ModelProfile(QWEN, "Qwen Image 2.1 Pro", 3, limit, ("2K",), ("auto",))


def _image(path: Path, image_format: str) -> Path:
    Image.new("RGB", (256, 256), (20, 40, 60)).save(path, image_format)
    return path


def _generated_jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), (20, 40, 60)).save(output, "JPEG")
    return output.getvalue()


def _config(tmp_path, source_dir, *, master="master prompt"):
    face = tmp_path / "face.png"
    body = tmp_path / "body.png"
    _image(face, "PNG")
    _image(body, "PNG")
    return BatchConfig(source_dir, tmp_path / "outputs", master, face, body)


def test_default_prompt_model_is_deepseek_v4_1_flash_uncensored():
    assert DEFAULT_VISION_MODEL == "deepseek-v4-1-flash-uncensored"


class FakeBatchClient:
    def __init__(self, *, analyze_error=None, stop_event=None):
        self.analyze_error = analyze_error
        self.stop_event = stop_event
        self.analyze_calls = []
        self.edit_payloads = []

    def analyze_photo(self, *, model, master_prompt, image_bytes, mime_type):
        self.analyze_calls.append((model, master_prompt, image_bytes, mime_type))
        if callable(self.analyze_error):
            self.analyze_error(Path(self.analyze_calls[-1][2]), len(self.analyze_calls))
        elif self.analyze_error is not None:
            raise self.analyze_error
        return f"prompt-{len(self.analyze_calls)}"

    def multi_edit(self, payload):
        self.edit_payloads.append(payload)
        if self.stop_event is not None:
            self.stop_event.set()
        return VeniceImageResult(_generated_jpeg(), "image/jpeg", "request", {})


def test_run_batch_reconstructs_each_photo_with_only_ordered_identity_references(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    source_a = _image(source_dir / "same.jpg", "JPEG")
    source_b = _image(source_dir / "same.png", "PNG")
    client = FakeBatchClient()

    output_dir = run_batch(client, _profile(), _config(tmp_path, source_dir))

    assert [call[0] for call in client.analyze_calls] == [DEFAULT_VISION_MODEL, DEFAULT_VISION_MODEL]
    assert [call[3] for call in client.analyze_calls] == ["image/jpeg", "image/png"]
    assert len(client.edit_payloads) == 2
    face_bytes = (tmp_path / "face.png").read_bytes()
    body_bytes = (tmp_path / "body.png").read_bytes()
    for payload in client.edit_payloads:
        decoded = [base64.b64decode(value) for value in payload["images"]]
        assert decoded == [face_bytes, body_bytes]
        assert decoded not in ([source_a.read_bytes()], [source_b.read_bytes()])
        assert payload["modelId"] == QWEN
        assert payload["resolution"] == "2K"
        assert payload["aspect_ratio"] == "auto"
        assert payload["output_format"] == "jpeg"
        assert payload["safe_mode"] is False
    prompt_files = sorted(output_dir.glob("*.prompt.txt"))
    image_files = sorted(output_dir.glob("*.jpg"))
    assert [path.read_text(encoding="utf-8") for path in prompt_files] == ["prompt-1", "prompt-2"]
    assert len(image_files) == 2
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert [item["source"] for item in manifest["items"]] == ["same.jpg", "same.png"]
    assert [item["status"] for item in manifest["items"]] == ["completed", "completed"]
    assert len({item["prompt_file"] for item in manifest["items"]}) == 2
    assert len({item["output_file"] for item in manifest["items"]}) == 2


def test_run_batch_rejects_empty_folder_before_provider_calls(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    client = FakeBatchClient()
    with pytest.raises(ValueError, match="No supported photos"):
        run_batch(client, _profile(), _config(tmp_path, source_dir))
    assert client.analyze_calls == []
    assert client.edit_payloads == []


def test_run_batch_rejects_missing_fixed_qwen_options_before_vision_call(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _image(source_dir / "photo.jpg", "JPEG")
    profile = _profile()
    profile = ModelProfile(profile.id, profile.label, profile.max_input_images,
                           profile.prompt_character_limit, ("1K",), ("auto",))
    client = FakeBatchClient()
    with pytest.raises(ValueError, match="2K"):
        run_batch(client, profile, _config(tmp_path, source_dir))
    assert client.analyze_calls == []


def test_run_batch_preflight_error_does_not_expose_reference_path(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _image(source_dir / "photo.jpg", "JPEG")
    config = _config(tmp_path, source_dir)
    missing = tmp_path / "private-missing-face.png"
    config = replace(config, face_path=missing)
    with pytest.raises(ValueError) as caught:
        run_batch(FakeBatchClient(), _profile(), config)
    assert str(tmp_path) not in str(caught.value)


def test_run_batch_sanitizes_output_run_paths_from_manifest_errors(tmp_path, monkeypatch):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _image(source_dir / "photo.jpg", "JPEG")
    client = FakeBatchClient()
    captured = []

    def fail_write(path, content):
        captured.append(Path(path))
        raise OSError(f"cannot write {path}")

    monkeypatch.setattr(batch_module, "_atomic_write_bytes", fail_write)
    output_dir = run_batch(client, _profile(), _config(tmp_path, source_dir))
    encoded = (output_dir / "manifest.json").read_text(encoding="utf-8")
    assert captured
    assert str(captured[0].parent) not in encoded


def test_run_batch_continues_after_photo_specific_error(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _image(source_dir / "bad.jpg", "JPEG")
    _image(source_dir / "good.png", "PNG")
    client = FakeBatchClient()
    original = client.analyze_photo

    def analyze(**kwargs):
        if kwargs["image_bytes"] == (source_dir / "bad.jpg").read_bytes():
            raise ValueError("invalid source")
        return original(**kwargs)

    client.analyze_photo = analyze
    output_dir = run_batch(client, _profile(), _config(tmp_path, source_dir))
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in manifest["items"]] == ["failed", "completed"]
    assert len(client.edit_payloads) == 1


@pytest.mark.parametrize("status", [401, 402, 429])
def test_run_batch_stops_after_fatal_provider_status(tmp_path, status):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _image(source_dir / "first.jpg", "JPEG")
    _image(source_dir / "second.jpg", "JPEG")
    client = FakeBatchClient(analyze_error=VeniceApiError(f"HTTP {status}", status_code=status))
    output_dir = run_batch(client, _profile(), _config(tmp_path, source_dir))
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert len(client.analyze_calls) == 1
    assert manifest["items"][0]["status"] == "failed"
    assert manifest["items"][1]["status"] == "stopped"


def test_run_batch_does_not_retry_timeout(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _image(source_dir / "first.jpg", "JPEG")
    client = FakeBatchClient(analyze_error=VeniceApiError("Venice request timed out."))
    output_dir = run_batch(client, _profile(), _config(tmp_path, source_dir))
    assert len(client.analyze_calls) == 1
    assert json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))["items"][0]["status"] == "failed"


def test_run_batch_stops_after_truncated_vision_response(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _image(source_dir / "first.jpg", "JPEG")
    _image(source_dir / "second.jpg", "JPEG")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "length", "message": {"content": "partial"}}]
        })

    client = VeniceClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    output_dir = run_batch(client, _profile(), _config(tmp_path, source_dir))
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert len(requests) == 1
    assert [item["status"] for item in manifest["items"]] == ["failed", "stopped"]


def test_run_batch_saves_too_long_prompt_without_edit_call(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _image(source_dir / "photo.jpg", "JPEG")
    client = FakeBatchClient()
    client.analyze_photo = lambda **kwargs: "x" * 11
    output_dir = run_batch(client, _profile(limit=10), _config(tmp_path, source_dir))
    assert (output_dir / "0001_photo.prompt.txt").read_text(encoding="utf-8") == "x" * 11
    assert client.edit_payloads == []
    item = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))["items"][0]
    assert item["status"] == "failed"
    assert "10" in item["error"]


def test_run_batch_stop_finishes_current_photo_and_prevents_next(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _image(source_dir / "first.jpg", "JPEG")
    _image(source_dir / "second.jpg", "JPEG")
    stop_event = threading.Event()
    client = FakeBatchClient(stop_event=stop_event)
    output_dir = run_batch(client, _profile(), _config(tmp_path, source_dir), stop_event=stop_event)
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert len(client.edit_payloads) == 1
    assert [item["status"] for item in manifest["items"]] == ["completed", "stopped"]


def test_run_batch_manifest_excludes_secrets_base64_and_absolute_paths(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _image(source_dir / "photo.jpg", "JPEG")
    client = FakeBatchClient(analyze_error=ValueError("Bearer secret-key private-base64-token"))
    output_dir = run_batch(client, _profile(), _config(tmp_path, source_dir, master="private master"))
    encoded = (output_dir / "manifest.json").read_text(encoding="utf-8")
    assert "secret-key" not in encoded
    assert "private-base64-token" not in encoded
    assert str(tmp_path) not in encoded


def test_run_batch_does_not_mark_non_image_response_completed(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _image(source_dir / "photo.jpg", "JPEG")
    client = FakeBatchClient()
    client.multi_edit = lambda payload: VeniceImageResult(b"generated-image", "image/jpeg", "request", {})
    output_dir = run_batch(client, _profile(), _config(tmp_path, source_dir))
    item = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))["items"][0]
    assert item["status"] == "failed"
    assert item["output_file"] is None
    assert not list(output_dir.glob("*.jpg"))


def test_run_batch_rejects_image_bytes_that_disagree_with_content_type(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _image(source_dir / "photo.jpg", "JPEG")
    png = BytesIO()
    Image.new("RGB", (32, 32)).save(png, "PNG")
    client = FakeBatchClient()
    client.multi_edit = lambda payload: VeniceImageResult(png.getvalue(), "image/jpeg", "request", {})
    output_dir = run_batch(client, _profile(), _config(tmp_path, source_dir))
    item = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))["items"][0]
    assert item["status"] == "failed"
    assert not list(output_dir.glob("*.jpg"))
