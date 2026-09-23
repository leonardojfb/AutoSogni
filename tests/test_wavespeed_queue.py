import json

from app.wavespeed.queue import QueueStatus, WaveSpeedQueue, WaveSpeedQueueItem, WaveSpeedQueueStore
from app.wavespeed.schemas import WaveSpeedPrediction


class FakeWaveSpeedClient:
    def __init__(self, events, results):
        self.events = events
        self.results = results
        self.counter = 0

    def estimate_price(self, payload):
        self.events.append(("price", payload["prompt"]))
        return {"price": 0.5}

    def submit(self, payload):
        self.counter += 1
        task_id = f"pred-{self.counter}"
        self.events.append(("submit", payload["prompt"]))
        return WaveSpeedPrediction(task_id, "created")

    def get_result(self, task_id):
        self.events.append(("get", task_id))
        return self.results[task_id]

    def poll_result(self, task_id, *, sleep=None, on_update=None, cancel_event=None):
        result = self.results[task_id]
        if on_update:
            on_update(result)
        return result


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


def test_runner_submits_one_item_at_a_time_and_continues_after_failure(tmp_path):
    events = []
    client = FakeWaveSpeedClient(
        events,
        results={
            "pred-1": WaveSpeedPrediction("pred-1", "failed", error="bad video"),
            "pred-2": WaveSpeedPrediction("pred-2", "completed", outputs=["url-2"]),
        },
    )
    queue = WaveSpeedQueue(
        frame_path="frame.png",
        output_dir=str(tmp_path),
        items=[
            WaveSpeedQueueItem(item_id="1", video_path="one.mp4", prompt="One"),
            WaveSpeedQueueItem(item_id="2", video_path="two.mp4", prompt="Two"),
        ],
    )

    from app.wavespeed.queue_runner import WaveSpeedQueueRunner

    result = WaveSpeedQueueRunner(
        client,
        upload_file=lambda path: {"download_url": f"https://cdn/{path}"},
        save_output=lambda output, output_dir, task_id: str(tmp_path / f"{task_id}.mp4"),
        on_update=lambda item: events.append(("update", item.item_id, item.status)),
        sleep=lambda _seconds: None,
    ).run(queue)

    assert [event for event in events if event[0] == "submit"] == [
        ("submit", "One"),
        ("submit", "Two"),
    ]
    assert result.items[0].status == QueueStatus.FAILED
    assert result.items[1].status == QueueStatus.COMPLETED


def test_runner_reuses_shared_frame_upload_and_honors_pause(tmp_path):
    events = []
    client = FakeWaveSpeedClient(
        events,
        results={
            "pred-1": WaveSpeedPrediction("pred-1", "completed", outputs=["url-1"]),
            "pred-2": WaveSpeedPrediction("pred-2", "completed", outputs=["url-2"]),
        },
    )
    uploaded = []
    queue = WaveSpeedQueue(
        frame_path="frame.png",
        output_dir=str(tmp_path),
        items=[
            WaveSpeedQueueItem(item_id="1", video_path="one.mp4", prompt="One"),
            WaveSpeedQueueItem(item_id="2", video_path="two.mp4", prompt="Two"),
        ],
    )

    def upload(path):
        uploaded.append(str(path))
        return {"download_url": f"https://cdn/{path}"}

    from app.wavespeed.queue_runner import WaveSpeedQueueRunner

    WaveSpeedQueueRunner(
        client,
        upload_file=upload,
        save_output=lambda output, output_dir, task_id: str(tmp_path / f"{task_id}.mp4"),
        sleep=lambda _seconds: None,
    ).run(queue)

    assert uploaded.count("frame.png") == 1
    assert uploaded.count("one.mp4") == 1
    assert uploaded.count("two.mp4") == 1

    queue.paused = True
    client.events.clear()
    WaveSpeedQueueRunner(client, upload_file=upload, save_output=lambda *_args: "").run(queue)
    assert not any(event[0] == "submit" for event in client.events)


def test_store_recovers_inflight_items_as_pending_without_losing_task_id(tmp_path):
    store = WaveSpeedQueueStore(tmp_path / "queue.json")
    queue = WaveSpeedQueue(
        items=[
            WaveSpeedQueueItem(
                item_id="1",
                video_path="ref.mp4",
                prompt="Walk",
                status=QueueStatus.RUNNING,
                task_id="pred-1",
            )
        ]
    )

    store.save(queue)
    restored = store.load()

    assert restored.items[0].status == QueueStatus.PENDING
    assert restored.items[0].task_id == "pred-1"


def test_runner_recovers_existing_task_without_duplicate_submission(tmp_path):
    events = []
    client = FakeWaveSpeedClient(
        events,
        results={"pred-existing": WaveSpeedPrediction("pred-existing", "completed", outputs=["url-1"])},
    )
    queue = WaveSpeedQueue(
        frame_path="frame.png",
        output_dir=str(tmp_path),
        items=[
            WaveSpeedQueueItem(
                item_id="1",
                video_path="one.mp4",
                prompt="One",
                task_id="pred-existing",
            )
        ],
    )

    from app.wavespeed.queue_runner import WaveSpeedQueueRunner

    result = WaveSpeedQueueRunner(
        client,
        upload_file=lambda path: {"download_url": f"https://cdn/{path}"},
        save_output=lambda output, output_dir, task_id: str(tmp_path / f"{task_id}.mp4"),
        sleep=lambda _seconds: None,
    ).run(queue)

    assert ("get", "pred-existing") in events
    assert not any(event[0] == "submit" for event in events)
    assert result.items[0].status == QueueStatus.COMPLETED
