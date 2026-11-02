import json
from pathlib import Path

import httpx
import pytest

from app.sogni.auth import ApiKeyStore
from app.venice.client import VeniceApiError, VeniceClient
from app.venice.validation import (
    ModelProfile,
    REQUESTED_MODEL_IDS,
    build_multi_edit_payload,
    parse_model_catalog,
    validate_local_image,
    validate_prompt_and_images,
)


def _entry(model_id, name, constraints):
    return {"id": model_id, "model_spec": {"name": name, "constraints": constraints}}


def _catalog_constraints(max_images, limit, resolutions, ratios, default=""):
    constraints = {
        "maxInputImages": max_images,
        "promptCharacterLimit": limit,
        "aspectRatios": ratios,
    }
    if resolutions:
        constraints["resolutions"] = resolutions
    if default:
        constraints["defaultResolution"] = default
    return constraints


def _catalog():
    return {"data": [
        _entry("qwen-image-2-1-pro-edit", "Qwen Image 2.1 Pro", _catalog_constraints(
            10, 10000, ["1K", "2K"], ["auto", "1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"], "1K")),
        _entry("nano-banana-pro-edit", "Nano Banana Pro", _catalog_constraints(
            6, 32768, ["1K", "2K", "4K"], ["auto", "1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"], "1K")),
        _entry("seedream-v5-pro-edit", "Seedream V5 Pro", _catalog_constraints(
            6, 10000, ["1K", "2K"], ["auto", "1:1", "3:2", "16:9", "9:16", "2:3", "3:4", "4:3"], "1K")),
        _entry("flux-2-max-edit", "FLUX 2 Max", _catalog_constraints(
            6, 3000, [], ["auto", "1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"])),
        _entry("unrelated-model", "Other", {}),
    ]}


def _profile(model_id="qwen-image-2-1-pro-edit", **overrides):
    values = dict(id=model_id, label="Qwen", max_input_images=10,
                  prompt_character_limit=10000, resolutions=("1K", "2K"),
                  aspect_ratios=("auto", "1:1"), default_resolution="1K")
    values.update(overrides)
    return ModelProfile(**values)


def test_catalog_request_uses_venice_base_url_and_bearer_header():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=_catalog())

    client = VeniceClient("secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    profiles = client.list_edit_models()
    assert str(seen[0].url) == "https://api.venice.ai/api/v1/models?type=inpaint"
    assert seen[0].headers["Authorization"] == "Bearer secret"
    assert tuple(profiles) == REQUESTED_MODEL_IDS
    assert profiles["qwen-image-2-1-pro-edit"] == parse_model_catalog(_catalog())["qwen-image-2-1-pro-edit"]


def test_multi_edit_returns_binary_result_without_rewriting_payload():
    payload = {"modelId": "qwen-image-2-1-pro-edit", "prompt": "Exact", "images": ["a", "b"]}
    seen = []

    def handler(request):
        seen.append(request)
        assert str(request.url) == "https://api.venice.ai/api/v1/image/multi-edit"
        assert json.loads(request.content) == payload
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(200, content=b"png-bytes", headers={
            "content-type": "image/png", "x-request-id": "req-1"})

    result = VeniceClient("secret", client=httpx.Client(transport=httpx.MockTransport(handler))).multi_edit(payload)
    assert len(seen) == 1
    assert result.content == b"png-bytes"
    assert result.content_type == "image/png"
    assert result.request_id == "req-1"
    assert result.headers["x-request-id"] == "req-1"
    assert "secret" not in repr(result)


def test_multi_edit_redacts_key_if_provider_echoes_it_in_response_header():
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, content=b"image", headers={"content-type": "image/png", "x-debug": "Bearer secret"}))
    result = VeniceClient("secret", client=httpx.Client(transport=transport)).multi_edit({})
    assert "secret" not in repr(result)
    assert result.headers["x-debug"] == "Bearer [redacted]"


@pytest.mark.parametrize("status", [400, 401, 402, 413, 415, 422, 429, 500, 503])
def test_multi_edit_translates_http_errors_without_leaking_key_or_payload(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"code": "bad_request", "message": "secret invalid image"}})

    client = VeniceClient("secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(VeniceApiError) as caught:
        client.multi_edit({"images": ["private-base64"]})
    assert len(calls) == 1
    assert caught.value.status_code == status
    assert str(status) in str(caught.value)
    assert "secret" not in str(caught.value)
    assert "private-base64" not in str(caught.value)


def test_multi_edit_translates_timeout_without_retry():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret timeout", request=request)

    client = VeniceClient("secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(VeniceApiError) as caught:
        client.multi_edit({"images": ["private-base64"]})
    assert len(calls) == 1
    assert caught.value.status_code is None
    assert "timed out" in str(caught.value).lower()
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("content_type", ["image/png", "image/jpeg", "image/webp"])
def test_multi_edit_accepts_supported_binary_types(content_type):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"image", headers={"content-type": content_type}))
    result = VeniceClient(client=httpx.Client(transport=transport)).multi_edit({})
    assert result.content_type == content_type
    assert result.content == b"image"
    assert result.request_id == ""


def test_multi_edit_rejects_non_image_success_response():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"error": "private-base64"}))
    with pytest.raises(VeniceApiError, match="content type") as caught:
        VeniceClient(client=httpx.Client(transport=transport)).multi_edit({})
    assert caught.value.status_code == 200
    assert "private-base64" not in str(caught.value)


def test_venice_key_store_reads_only_its_configured_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SOGNI_API_KEY", "sogni-secret")
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    store = ApiKeyStore(tmp_path / "venice_api_key.txt", env_name="VENICE_API_KEY")
    assert store.get() == ""
    store.save("file-secret")
    assert store.get() == "file-secret"
    monkeypatch.setenv("VENICE_API_KEY", "venice-secret")
    assert store.get() == "venice-secret"
    assert ApiKeyStore(tmp_path / "missing.txt").get() == "sogni-secret"


def test_parse_model_catalog_keeps_only_requested_edit_models():
    profiles = parse_model_catalog(_catalog())
    assert tuple(profiles) == REQUESTED_MODEL_IDS
    assert [(p.label, p.max_input_images, p.prompt_character_limit, p.resolutions,
             p.aspect_ratios, p.default_resolution) for p in profiles.values()] == [
        ("Qwen Image 2.1 Pro", 10, 10000, ("1K", "2K"),
         ("auto", "1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"), "1K"),
        ("Nano Banana Pro", 6, 32768, ("1K", "2K", "4K"),
         ("auto", "1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"), "1K"),
        ("Seedream V5 Pro", 6, 10000, ("1K", "2K"),
         ("auto", "1:1", "3:2", "16:9", "9:16", "2:3", "3:4", "4:3"), "1K"),
        ("FLUX 2 Max", 6, 3000, (),
         ("auto", "1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"), ""),
    ]
    assert "unrelated-model" not in profiles


def test_payload_preserves_prompt_and_reference_order():
    assert build_multi_edit_payload(
        profile=_profile(), prompt="  Keep this exact text.  ",
        images=["base64-a", "base64-b", "base64-c"], aspect_ratio="1:1",
        resolution="2K", output_format="webp", safe_mode=False,
    ) == {
        "modelId": "qwen-image-2-1-pro-edit", "prompt": "  Keep this exact text.  ",
        "images": ["base64-a", "base64-b", "base64-c"], "aspect_ratio": "1:1",
        "resolution": "2K", "output_format": "webp", "safe_mode": False,
    }


def test_payload_omits_unset_optional_options():
    assert build_multi_edit_payload(profile=_profile(), prompt="Edit", images=["a"]) == {
        "modelId": "qwen-image-2-1-pro-edit", "prompt": "Edit", "images": ["a"],
        "output_format": "png", "safe_mode": True,
    }


@pytest.mark.parametrize("kwargs, message", [
    ({"prompt": "  "}, "Prompt"),
    ({"images": []}, "image"),
    ({"images": ["a"] * 11}, "at most 10"),
    ({"prompt": "x" * 10001}, "10000"),
    ({"aspect_ratio": "5:4"}, "aspect ratio"),
    ({"resolution": "4K"}, "resolution"),
    ({"output_format": "bmp"}, "Output format"),
])
def test_payload_rejects_invalid_options(kwargs, message):
    values = {"profile": _profile(), "prompt": "Edit", "images": ["a"]}
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        build_multi_edit_payload(**values)


def test_validate_prompt_and_images_checks_prompt_count_and_files(tmp_path):
    path = tmp_path / "valid.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (256).to_bytes(4, "big") * 2)
    validate_prompt_and_images(profile=_profile(), prompt="Edit", image_paths=[path])
    with pytest.raises(ValueError, match="at most 10"):
        validate_prompt_and_images(profile=_profile(), prompt="Edit", image_paths=[path] * 11)
    with pytest.raises(ValueError, match="Prompt"):
        validate_prompt_and_images(profile=_profile(), prompt="  ", image_paths=[path])


def test_validate_prompt_and_images_enforces_combined_pixel_cap(tmp_path):
    path = tmp_path / "valid.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (5760).to_bytes(4, "big") * 2)
    with pytest.raises(ValueError, match="pixel"):
        validate_prompt_and_images(profile=_profile(), prompt="Edit", image_paths=[path, path])


@pytest.mark.parametrize("suffix,header", [
    ("png", b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (256).to_bytes(4, "big") * 2),
    ("gif", b"GIF89a" + (256).to_bytes(2, "little") * 2 + b"\0" * 20),
    ("webp", b"RIFF" + (30).to_bytes(4, "little") + b"WEBP" + b"VP8X" + (10).to_bytes(4, "little") + b"\0" * 4 + (255).to_bytes(3, "little") * 2),
    ("jpg", b"\xff\xd8\xff\xc0\x00\x11\x08" + (256).to_bytes(2, "big") * 2 + b"\x03" + b"\0" * 9 + b"\xff\xd9"),
])
def test_validate_local_image_reads_dimensions(tmp_path, suffix, header):
    path = tmp_path / f"image.{suffix}"
    path.write_bytes(header)
    info = validate_local_image(path)
    assert (info.width, info.height) == (256, 256)


def test_validate_local_image_rejects_invalid_size_dimensions_and_headers(tmp_path):
    tiny = tmp_path / "tiny.png"
    tiny.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (255).to_bytes(4, "big") * 2)
    with pytest.raises(ValueError, match="256"):
        validate_local_image(tiny)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"bad")
    with pytest.raises(ValueError, match="image|header|dimensions"):
        validate_local_image(bad)
    with pytest.raises(ValueError, match="does not exist"):
        validate_local_image(tmp_path / "missing.png")
    unsupported = tmp_path / "image.bmp"
    unsupported.write_bytes(b"x")
    with pytest.raises(ValueError, match="PNG|JPEG|WebP|GIF"):
        validate_local_image(unsupported)


def test_validate_local_image_rejects_pixel_cap_and_25mb(tmp_path):
    large = tmp_path / "large.png"
    large.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (5760).to_bytes(4, "big") + (5761).to_bytes(4, "big"))
    with pytest.raises(ValueError, match="pixel"):
        validate_local_image(large)
    too_big = tmp_path / "huge.png"
    with too_big.open("wb") as stream:
        stream.truncate(25 * 1024 * 1024)
    with pytest.raises(ValueError, match="25 MB"):
        validate_local_image(too_big)


def test_validate_local_image_uses_decimal_25mb_boundary(tmp_path):
    limit = 25_000_000
    header = b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (256).to_bytes(4, "big") * 2
    just_under = tmp_path / "just-under.png"
    just_under.write_bytes(header)
    with just_under.open("r+b") as stream:
        stream.truncate(limit - 1)
    assert validate_local_image(just_under).size_bytes == limit - 1

    at_limit = tmp_path / "at-limit.png"
    at_limit.write_bytes(header)
    with at_limit.open("r+b") as stream:
        stream.truncate(limit)
    with pytest.raises(ValueError, match="25 MB"):
        validate_local_image(at_limit)
