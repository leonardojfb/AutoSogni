import json

import httpx
import pytest

from app.byteplus.client import BytePlusClient
from app.byteplus.validation import build_task_payload
from app.byteplus.queue import BytePlusCampaign, BytePlusCampaignStore, BytePlusQueue, BytePlusQueueItem, QueueStatus


def test_builds_first_and_last_frame_payload_with_all_supported_controls():
    payload = build_task_payload({
        "mode": "first_last_frame", "prompt": "A quiet turn", "first_frame_url": "https://cdn/first.png",
        "last_frame_url": "https://cdn/last.png", "generate_audio": False, "seed": 7,
        "resolution": "720p", "ratio": "16:9", "duration": 8, "watermark": False,
        "return_last_frame": True, "callback_url": "https://example.test/hook", "execution_expires_after": 3600,
    })
    assert payload == {
        "model": "dreamina-seedance-2-0-260128", "content": [
            {"type": "text", "text": "A quiet turn"},
            {"type": "image_url", "image_url": {"url": "https://cdn/first.png"}, "role": "first_frame"},
            {"type": "image_url", "image_url": {"url": "https://cdn/last.png"}, "role": "last_frame"},
        ], "generate_audio": False, "seed": 7, "resolution": "720p", "ratio": "16:9", "duration": 8,
        "watermark": False, "return_last_frame": True, "callback_url": "https://example.test/hook",
        "execution_expires_after": 3600,
    }


def test_client_posts_to_modelark_and_polls_completed_task(tmp_path):
    seen = []
    states = iter(["running", "succeeded"])

    def handler(request):
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "cgt-1"})
        status = next(states)
        body = {"id": "cgt-1", "status": status}
        if status == "succeeded":
            body["content"] = {"video_url": "https://cdn/video.mp4"}
        return httpx.Response(200, json=body)

    client = BytePlusClient("secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
    task = client.submit({"mode": "text", "prompt": "Scene"})
    result = client.poll_task(task.id, sleep=lambda _: None, timeout=1)
    assert task.id == "cgt-1"
    assert result.video_url == "https://cdn/video.mp4"
    assert seen[0].url.path == "/api/v3/contents/generations/tasks"
    assert seen[0].headers["Authorization"] == "Bearer secret"
    assert json.loads(seen[0].content)["model"] == "dreamina-seedance-2-0-260128"


def test_invalid_omni_audio_only_and_image_1080_are_rejected():
    with pytest.raises(ValueError, match="audio"):
        build_task_payload({"mode": "omni", "prompt": "Scene", "reference_audios": ["https://cdn/a.mp3"]})
    with pytest.raises(ValueError, match="1080p"):
        build_task_payload({"mode": "first_frame", "prompt": "Scene", "first_frame_url": "https://cdn/a.png", "resolution": "1080p"})


def test_campaign_store_recovers_running_item_without_losing_task_id(tmp_path):
    store = BytePlusCampaignStore(tmp_path / "campaigns.json")
    campaign = BytePlusCampaign(name="Direct", queue=BytePlusQueue(items=[BytePlusQueueItem(snapshot={"mode": "text", "prompt": "A"}, status=QueueStatus.RUNNING, task_id="cgt-1")]))
    store.save(campaign, active=True)
    campaigns, active = store.load()
    assert active == campaign.campaign_id
    assert campaigns[0].queue.items[0].status == QueueStatus.PENDING
    assert campaigns[0].queue.items[0].task_id == "cgt-1"
