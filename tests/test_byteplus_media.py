import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog

from app.byteplus.client import BytePlusClient
from app.byteplus.https_uploads import HTTPSUploader
from app.byteplus.media import prepare_snapshot
from app.byteplus.uploads import BytePlusUploader
from app.byteplus.validation import build_task_payload
from app.byteplus.queue import BytePlusCampaign, BytePlusCampaignStore, BytePlusQueueItem
from app.ui.main_window import BYTEPLUS_HTTPS_UPLOAD_URL, MainWindow
from app.ui.byteplus_references import BytePlusReferences


def test_selected_local_files_reach_api_with_correct_types_and_queue_stays_local(tmp_path):
    image = tmp_path / "face.png"; image.write_bytes(b"png-image")
    video = tmp_path / "motion.mp4"; video.write_bytes(b"mp4-video")
    audio = tmp_path / "sound.wav"; audio.write_bytes(b"wav-audio")
    snapshot = {"mode": "omni", "prompt": "literal prompt", "reference_images": [str(image)],
                "reference_videos": [str(video)], "reference_audios": [str(audio)]}
    uploaded, requests = [], []
    class Uploader:
        def upload(self, path, kind, group):
            uploaded.append((path, kind, group))
            return "https://storage.example/motion.mp4"
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "task-1"})
    client = BytePlusClient("test-key", client=httpx.Client(transport=httpx.MockTransport(respond)), uploader=Uploader())
    assert client.submit(snapshot).id == "task-1"
    content = requests[0]["content"]
    assert [entry["type"] for entry in content] == ["text", "image_url", "video_url", "audio_url"]
    assert content[1]["image_url"]["url"].endswith(base64.b64encode(b"png-image").decode())
    assert content[2]["video_url"]["url"] == "https://storage.example/motion.mp4"
    assert content[3]["audio_url"]["url"].startswith("data:audio/")
    assert uploaded == [(video, "video", "")]
    campaign = BytePlusCampaign(); campaign.queue.items.append(BytePlusQueueItem(snapshot=snapshot))
    store = BytePlusCampaignStore(tmp_path / "queue.json"); store.save(campaign)
    assert store.load()[0][0].queue.items[0].snapshot == snapshot
    assert snapshot["reference_images"] == [str(image)]


def test_https_uploader_returns_public_url_for_local_video(tmp_path):
    video = tmp_path / "reference.mp4"
    video.write_bytes(b"video-bytes")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"url": "https://uploader.example/files/video-1.mp4"})

    uploader = HTTPSUploader("https://uploader.example", "upload-token",
                             client=httpx.Client(transport=httpx.MockTransport(respond)))

    assert uploader.upload(video, "video") == "https://uploader.example/files/video-1.mp4"
    assert requests[0].method == "POST"
    assert requests[0].headers["Authorization"] == "Bearer upload-token"
    assert b"video-bytes" in requests[0].read()


def test_https_uploader_rejects_insecure_endpoint(tmp_path):
    video = tmp_path / "reference.mp4"
    video.write_bytes(b"video-bytes")
    uploader = HTTPSUploader("http://uploader.example", "upload-token")

    with pytest.raises(ValueError, match="HTTPS pública"):
        uploader.upload(video, "video")


@pytest.mark.parametrize("settings", [{}, {"provider": ""}])
def test_legacy_upload_settings_default_to_https_uploader(settings):
    uploader = MainWindow._byteplus_build_uploader(settings)

    assert isinstance(uploader, HTTPSUploader)
    assert uploader.endpoint == BYTEPLUS_HTTPS_UPLOAD_URL


def test_file_picker_order_roles_and_removal(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    widget = BytePlusReferences()
    paths = [tmp_path / "first.png", tmp_path / "last.png"]
    for path in paths: path.write_bytes(b"image")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([str(p) for p in paths], ""))
    widget.choose("image")
    widget.lists["image"].setCurrentRow(1); widget.move("image", -1)
    assert widget.snapshot(2) == {"first_frame_url": str(paths[1]), "last_frame_url": str(paths[0])}
    widget.remove("image")
    assert widget.snapshot(1) == {"first_frame_url": str(paths[0])}
    widget.add_reference("video", "asset://video-1")
    assert widget.snapshot(3)["reference_videos"] == ["asset://video-1"]
    assert widget.snapshot(4) == {"reference_video_url": "asset://video-1"}
    assert widget.snapshot(0) == {}
    widget.set_mode(3)
    assert not widget.boxes["video"].isHidden()
    widget.close()


def test_missing_or_wrong_files_rejected_before_upload(tmp_path):
    missing = {"mode": "first_frame", "prompt": "x", "first_frame_url": str(tmp_path / "no.png")}
    with pytest.raises(ValueError, match="No existe"):
        prepare_snapshot(missing, validate_only=True)
    wrong = tmp_path / "file.mp4"; wrong.write_bytes(b"video")
    missing["first_frame_url"] = str(wrong)
    with pytest.raises(ValueError, match="Formato"):
        prepare_snapshot(missing, validate_only=True)


def test_local_video_requires_storage_and_validation_does_not_upload(tmp_path):
    video = tmp_path / "video.mp4"; video.write_bytes(b"video")
    snapshot = {"mode": "edit", "prompt": "edit", "reference_video_url": str(video)}
    assert build_task_payload(prepare_snapshot(snapshot, validate_only=True))
    with pytest.raises(ValueError, match="configurá"):
        prepare_snapshot(snapshot)


def test_asset_ingestion_waits_for_active_and_signs_separately(tmp_path):
    video = tmp_path / "video.mp4"; video.write_bytes(b"video")
    calls, stored = [], []
    statuses = iter(["Processing", "Active"])
    class Storage:
        def put_object_from_file(self, bucket, key, path, **kwargs):
            stored.append((bucket, key, path))
        def pre_signed_url(self, *args, **kwargs):
            return SimpleNamespace(signed_url="https://storage.example/video.mp4?signature=x")
    def respond(request):
        body = json.loads(request.content)
        calls.append((request.url.params["Action"], body))
        assert request.headers["Authorization"].startswith("HMAC-SHA256 Credential=access/")
        result = {"Id": "asset-1"} if len(calls) == 1 else {"Status": next(statuses)}
        return httpx.Response(200, json={"Result": result})
    uploader = BytePlusUploader({"access_key": "access", "secret_key": "secret", "bucket": "bucket"},
        client=httpx.Client(transport=httpx.MockTransport(respond)), storage=Storage(), sleep=lambda _: None)
    assert uploader.upload(video, "video", "group-1") == "asset://asset-1"
    assert calls[0][1]["AssetType"] == "Video"
    assert calls[0][1]["GroupId"] == "group-1"
    assert [c[0] for c in calls] == ["CreateAsset", "GetAsset", "GetAsset"]
    assert len(stored) == 1
