import json
from pathlib import Path

import httpx
import pytest

from app.wavespeed.client import WaveSpeedClient
from app.wavespeed.history import WaveSpeedHistoryStore
from app.wavespeed.validation import (
    SEEDANCE_MODEL_ID,
    SEEDANCE_I2V_SPICY_MODEL_ID,
    FLUX_MODEL_ID,
    FACE_SWAP_MODEL_ID,
    build_seedance_payload,
    build_seedance_i2v_spicy_payload,
    build_flux_payload,
    build_face_swap_payload,
    build_reference_video_payload,
    validate_local_reference_file,
    validate_request,
)


def test_seedance_accepts_text_only_and_uses_only_its_documented_fields():
    payload = build_seedance_payload(prompt="Exact scene", duration=15, resolution="4k",
                                     aspect_ratio="21:9", enable_web_search=True, generate_audio=False)
    assert payload == {"prompt": "Exact scene", "resolution": "4k", "aspect_ratio": "21:9",
                       "duration": 15, "enable_web_search": True, "generate_audio": False,
                       "safety_checker": True}
    with pytest.raises(ValueError, match="4 to 15"):
        build_seedance_payload(prompt="Scene", duration=16)
    with pytest.raises(ValueError, match="at most 3 reference_videos"):
        build_seedance_payload(prompt="Scene", reference_videos=["url"] * 4)


def test_seedance_spicy_builds_exact_image_to_video_fields():
    payload = build_seedance_i2v_spicy_payload(
        image="https://cdn.test/start.png",
        last_image="https://cdn.test/end.png",
        prompt="Walk toward the camera",
        aspect_ratio="21:9",
        resolution="4k",
        duration=15,
        generate_audio=False,
        seed=42,
    )

    assert payload == {
        "image": "https://cdn.test/start.png",
        "last_image": "https://cdn.test/end.png",
        "prompt": "Walk toward the camera",
        "aspect_ratio": "21:9",
        "resolution": "4k",
        "duration": 15,
        "generate_audio": False,
        "seed": 42,
        "safety_checker": True,
    }


def test_seedance_spicy_omits_automatic_ratio_and_requires_start_image():
    assert build_seedance_i2v_spicy_payload(image="https://cdn.test/start.png") == {
        "image": "https://cdn.test/start.png",
        "resolution": "720p",
        "duration": 5,
        "generate_audio": True,
        "safety_checker": True,
    }
    with pytest.raises(ValueError, match="requires a public start-image URL"):
        build_seedance_i2v_spicy_payload(image="")


def test_flux_requires_images_and_keeps_optional_size():
    assert build_flux_payload(prompt="Edit", images=["https://cdn/image.png"], size="1024*1024", seed=7) == {
        "prompt": "Edit", "images": ["https://cdn/image.png"], "size": "1024*1024", "seed": 7,
        "enable_sync_mode": False, "enable_base64_output": False,
    }
    with pytest.raises(ValueError, match="1 to 3"):
        build_flux_payload(prompt="Edit", images=[])


def test_face_swap_requires_base_and_identity_images():
    assert build_face_swap_payload(
        image="https://cdn/target.png", face_image="https://cdn/identity.png",
        target_index=0, target_gender="female", output_format="png",
    ) == {
        "image": "https://cdn/target.png", "face_image": "https://cdn/identity.png",
        "target_index": 0, "target_gender": "female", "output_format": "png",
        "enable_sync_mode": False, "enable_base64_output": False,
    }
    with pytest.raises(ValueError, match="base image"):
        build_face_swap_payload(image="", face_image="https://cdn/identity.png")


@pytest.mark.parametrize("model_id,payload", [
    (SEEDANCE_MODEL_ID, {"prompt": "Scene", "duration": 5, "resolution": "720p",
                         "aspect_ratio": "16:9", "enable_web_search": False, "generate_audio": True,
                         "safety_checker": False}),
    (SEEDANCE_I2V_SPICY_MODEL_ID, {"image": "https://cdn.test/start.png",
                                  "last_image": "https://cdn.test/end.png", "prompt": "Move",
                                  "duration": 8, "resolution": "1080p", "aspect_ratio": "9:16",
                                  "generate_audio": True, "seed": -1, "safety_checker": False}),
    (FLUX_MODEL_ID, {"prompt": "Edit", "images": ["https://cdn/image.png"], "seed": -1,
                     "enable_sync_mode": False, "enable_base64_output": False}),
    (FACE_SWAP_MODEL_ID, {"image": "https://cdn/target.png", "face_image": "https://cdn/identity.png",
                          "target_index": 0, "target_gender": "female", "output_format": "png",
                          "enable_sync_mode": False, "enable_base64_output": False}),
])
def test_client_uses_selected_model_for_submit_and_price(model_id, payload):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        if request.url.path == "/api/v3/model/price":
            return httpx.Response(200, json={"code": 200, "data": {"price": 1}})
        return httpx.Response(200, json={"code": 200, "data": {"id": "pred", "status": "created"}})

    client = WaveSpeedClient("secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    client.estimate_price(payload, model_id=model_id)
    client.submit(payload, model_id=model_id)
    assert seen == [
        ("/api/v3/model/price", {"model_id": model_id, "inputs": payload}),
        (f"/api/v3/{model_id}", payload),
    ]


def test_validate_request_requires_reference_media_and_enforces_limits():
    with pytest.raises(ValueError, match="At least one reference"):
        validate_request({"prompt": "A scene"})

    with pytest.raises(ValueError, match="10 reference images"):
        validate_request(
            {
                "prompt": "A scene",
                "reference_images": [f"https://cdn.test/{index}.png" for index in range(11)],
            }
        )

    with pytest.raises(ValueError, match="video duration"):
        validate_request(
            {
                "prompt": "A scene",
                "reference_videos": ["https://cdn.test/ref.mp4"],
                "duration": 20,
                "reference_video_duration_seconds": 11,
            }
        )


def test_build_reference_video_payload_includes_model_and_api_toggles():
    payload = build_reference_video_payload(
        prompt="A person walks toward camera",
        reference_images=["https://cdn.test/person.png"],
        reference_videos=[],
        reference_audios=[],
        resolution="1080p",
        aspect_ratio="9:16",
        duration=7,
        enable_prompt_expansion=True,
        enable_audio=False,
        seed=42,
        enable_sync_mode=True,
        enable_base64_output=True,
    )

    assert payload == {
        "prompt": "A person walks toward camera",
        "reference_images": ["https://cdn.test/person.png"],
        "resolution": "1080p",
        "aspect_ratio": "9:16",
        "duration": 7,
        "enable_prompt_expansion": True,
        "enable_audio": False,
        "seed": 42,
        "enable_sync_mode": True,
        "enable_base64_output": True,
    }


def test_client_upload_submit_and_poll_never_sends_api_key_to_storage(tmp_path: Path):
    image = tmp_path / "person.png"
    image.write_bytes(b"png-bytes")
    statuses = iter(["processing", "completed"])
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v3/media/uploads":
            body = json.loads(request.content)
            assert body["filename"] == "person.png"
            assert request.headers["Authorization"] == "Bearer secret"
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "download_url": "https://cdn.test/person.png",
                        "upload": {
                            "method": "PUT",
                            "url": "https://storage.test/upload/1",
                            "headers": {"Content-Type": "image/png"},
                        },
                    },
                },
            )
        if request.url.host == "storage.test":
            assert "Authorization" not in request.headers
            assert request.content == b"png-bytes"
            return httpx.Response(200)
        if request.url.path == "/api/v3/alibaba/wan-3.0/reference-to-video":
            assert json.loads(request.content)["reference_images"] == ["https://cdn.test/person.png"]
            return httpx.Response(200, json={"code": 200, "data": {"id": "pred-1", "status": "created"}})
        if request.url.path == "/api/v3/predictions/pred-1/result":
            status = next(statuses)
            data = {"id": "pred-1", "status": status}
            if status == "completed":
                data["outputs"] = ["https://cdn.test/video.mp4"]
            return httpx.Response(200, json={"code": 200, "data": data})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = WaveSpeedClient("secret", client=http)

    media = client.upload_file(image)
    submitted = client.submit(
        {
            "prompt": "A scene",
            "reference_images": [media["download_url"]],
            "duration": 5,
        }
    )
    result = client.poll_result("pred-1", sleep=lambda _seconds: None, timeout=1)

    assert media["download_url"] == "https://cdn.test/person.png"
    assert submitted.id == "pred-1"
    assert result.status == "completed"
    assert result.outputs == ["https://cdn.test/video.mp4"]
    assert any(request.url.host == "storage.test" for request in seen)


def test_history_store_persists_safe_request_metadata_without_remote_upload_urls(tmp_path: Path):
    store = WaveSpeedHistoryStore(tmp_path / "history.json")

    store.add(
        {
            "task_id": "pred-1",
            "status": "completed",
            "payload": {
                "prompt": "A scene",
                "reference_images": ["https://storage.test/private-upload-token"],
            },
            "output_file": "C:/output/pred-1.mp4",
        }
    )

    rows = store.list()
    assert len(rows) == 1
    assert rows[0]["task_id"] == "pred-1"
    assert rows[0]["payload"]["reference_images"] == ["<remote-url>"]


def test_local_video_reference_validation_matches_model_constraints(tmp_path: Path):
    invalid = tmp_path / "reference.avi"
    invalid.write_bytes(b"video")

    with pytest.raises(ValueError, match="MP4 or MOV"):
        validate_local_reference_file(invalid, "reference_videos")
