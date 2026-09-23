import json

from app.wavespeed.queue import QueueStatus, WaveSpeedQueue, WaveSpeedQueueItem, WaveSpeedQueueStore


def test_queue_store_round_trips_items_and_redacts_remote_values(tmp_path):
    store = WaveSpeedQueueStore(tmp_path / "queue.json")
    queue = WaveSpeedQueue(
        frame_path="C:/inputs/frame.png",
        output_dir="C:/outputs",
        items=[WaveSpeedQueueItem(item_id="1", video_path="C:/inputs/ref.mp4", prompt="Walk")],
    )
    queue.items[0].task_id = "pred-1"
    queue.items[0].output_file = "C:/outputs/pred-1.mp4"

    store.save(queue)
    restored = store.load()

    assert restored.items[0].video_path == "C:/inputs/ref.mp4"
    assert restored.items[0].task_id == "pred-1"
    assert "https://" not in (tmp_path / "queue.json").read_text(encoding="utf-8")


def test_retry_resets_only_remote_result_fields():
    item = WaveSpeedQueueItem(item_id="1", video_path="ref.mp4", prompt="Walk")
    item.status = QueueStatus.FAILED
    item.task_id = "pred-1"
    item.price = 0.5
    item.output_file = "out.mp4"
    item.error = "remote failure"

    item.retry()

    assert item.status == QueueStatus.PENDING
    assert item.task_id == ""
    assert item.price is None
    assert item.output_file == ""
    assert item.error == ""
    assert item.video_path == "ref.mp4"
    assert item.prompt == "Walk"
