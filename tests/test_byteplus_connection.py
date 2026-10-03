from pathlib import Path

import httpx
import pytest
from PySide6.QtWidgets import QApplication

from app.byteplus.client import BytePlusApiError, BytePlusClient
from app.byteplus.queue import BytePlusQueue, BytePlusQueueItem
from app.byteplus.schemas import BytePlusTask
from app.ui.byteplus_queue import BytePlusQueueWidget


def test_connection_uses_authenticated_list_tasks_request():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    client = BytePlusClient("key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.test_connection() is True
    assert seen[0].url.path == "/api/v3/contents/generations/tasks"
    assert seen[0].headers["Authorization"] == "Bearer key"


@pytest.mark.parametrize("error_code", [
    "InputVideoSensitiveContentDetected.PrivacyInformation",
    "InputVideoSensitiveContentDetected.PolicyViolation",
    "InputVideoSensitiveContentDetected",
])
def test_api_error_preserves_full_moderation_code(error_code):
    def handler(_request):
        return httpx.Response(400, json={"error": {"code": error_code, "message": "Input video rejected."}})

    client = BytePlusClient("key", client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(BytePlusApiError) as caught:
        client._request("POST", "/contents/generations/tasks")

    assert caught.value.code == error_code
    assert str(caught.value) == f"{error_code}: Input video rejected."


def test_failed_task_preserves_full_moderation_code():
    task = BytePlusTask.from_payload({
        "id": "task-1",
        "status": "failed",
        "error": {
            "code": "InputVideoSensitiveContentDetected.PrivacyInformation",
            "message": "Input video rejected.",
        },
    })

    assert task.error_code == "InputVideoSensitiveContentDetected.PrivacyInformation"
    assert task.error == "Input video rejected."


def test_generate_and_download_saves_completed_video(tmp_path: Path):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "task-1", "status": "queued"})
        if request.url.path.endswith("/task-1"):
            return httpx.Response(200, json={
                "id": "task-1", "status": "succeeded",
                "content": {"video_url": "https://cdn.test/generated.mp4"},
            })
        return httpx.Response(200, content=b"generated-video")

    client = BytePlusClient("key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    output = client.generate_and_download({"mode": "text", "prompt": "scene"}, tmp_path)

    assert output == tmp_path / "byteplus_task-1.mp4"
    assert output.read_bytes() == b"generated-video"


def test_byteplus_queue_widget_shows_row_price_and_total():
    app = QApplication.instance() or QApplication([])
    item = BytePlusQueueItem(snapshot={"prompt": "scene"})
    widget = BytePlusQueueWidget()
    widget.load_queue(BytePlusQueue(items=[item]))

    widget.set_item_price(item.item_id, 1.515)
    widget.set_total_price(1.515)

    assert widget.table.item(0, 2).text() == "$1.5150"
    assert widget.total_price_label.text() == "Total estimado: $1.5150 USD"
