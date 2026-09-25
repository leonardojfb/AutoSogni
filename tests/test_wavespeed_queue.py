import json

from app.wavespeed.queue import (
    QueueStatus,
    WaveSpeedCampaign,
    WaveSpeedCampaignStore,
    WaveSpeedQueue,
    WaveSpeedQueueItem,
    WaveSpeedQueueStore,
)
from app.wavespeed.schemas import WaveSpeedPrediction
from app.wavespeed.validation import SEEDANCE_MODEL_ID


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
        self.events.append(("poll", task_id))
        result = self.results[task_id]
        if on_update:
            on_update(result)
        return result


def test_seedance_queue_persists_model_and_uses_seedance_endpoint(tmp_path):
    from app.wavespeed.queue_runner import WaveSpeedQueueRunner

    calls = []

    class Client:
        def estimate_price(self, payload, *, model_id):
            calls.append(("price", model_id, payload))
            return {"price": 1.0}

        def submit(self, payload, *, model_id):
            calls.append(("submit", model_id, payload))
            return WaveSpeedPrediction("pred-seedance", "completed", outputs=["url"])

    item = WaveSpeedQueueItem(video_path="video.mp4", prompt="Scene", model_id=SEEDANCE_MODEL_ID,
                              duration=15, resolution="4k", aspect_ratio="21:9", enable_prompt_expansion=True)
    queue = WaveSpeedQueue(frame_path="frame.png", output_dir=str(tmp_path), items=[item])
    store = WaveSpeedQueueStore(tmp_path / "queue.json")
    store.save(queue)
    restored = store.load()
    WaveSpeedQueueRunner(Client(), upload_file=lambda path: {"download_url": f"https://cdn/{path}"},
                         save_output=lambda *_args: "output.mp4").run(restored)

    assert restored.items[0].status == QueueStatus.COMPLETED
    assert [call[:2] for call in calls] == [("price", SEEDANCE_MODEL_ID), ("submit", SEEDANCE_MODEL_ID)]
    assert calls[1][2]["enable_web_search"] is True
    assert "seed" not in calls[1][2]


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


def test_queue_store_persists_parallel_execution_mode(tmp_path):
    store = WaveSpeedQueueStore(tmp_path / "queue.json")
    queue = WaveSpeedQueue(execution_mode="parallel")

    store.save(queue)

    assert store.load().execution_mode == "parallel"


def test_campaign_store_persists_named_queue_and_active_campaign(tmp_path):
    store = WaveSpeedCampaignStore(tmp_path / "campaigns.json")
    campaign = WaveSpeedCampaign(
        campaign_id="campaign-1",
        name="Frame azul",
        queue=WaveSpeedQueue(
            frame_path="C:/frames/blue.png",
            items=[WaveSpeedQueueItem(video_path="C:/videos/one.mp4", prompt="Walk")],
        ),
    )

    store.save(campaign, active=True)
    campaigns, active_id = store.load()

    assert active_id == "campaign-1"
    assert campaigns[0].name == "Frame azul"
    assert campaigns[0].queue.items[0].prompt == "Walk"
    assert "api_key" not in (tmp_path / "campaigns.json").read_text(encoding="utf-8")


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


def test_runner_estimates_each_row_and_returns_known_total(tmp_path):
    events = []
    client = FakeWaveSpeedClient(
        events,
        results={},
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

    total = WaveSpeedQueueRunner(
        client,
        upload_file=lambda path: {"download_url": f"https://cdn/{path}"},
        save_output=lambda *_args: "",
        sleep=lambda _seconds: None,
    ).estimate_prices(queue)

    assert total == 1.0
    assert [item.price for item in queue.items] == [0.5, 0.5]
    assert not any(event[0] == "submit" for event in events)


def test_runner_parallel_submits_all_jobs_before_polling(tmp_path):
    events = []
    client = FakeWaveSpeedClient(
        events,
        results={
            "pred-1": WaveSpeedPrediction("pred-1", "completed", outputs=["url-1"]),
            "pred-2": WaveSpeedPrediction("pred-2", "completed", outputs=["url-2"]),
        },
    )
    queue = WaveSpeedQueue(
        frame_path="frame.png",
        execution_mode="parallel",
        output_dir=str(tmp_path),
        items=[
            WaveSpeedQueueItem(item_id="1", video_path="one.mp4", prompt="One"),
            WaveSpeedQueueItem(item_id="2", video_path="two.mp4", prompt="Two"),
        ],
    )

    from app.wavespeed.queue_runner import WaveSpeedQueueRunner

    WaveSpeedQueueRunner(
        client,
        upload_file=lambda path: {"download_url": f"https://cdn/{path}"},
        save_output=lambda output, output_dir, task_id: str(tmp_path / f"{task_id}.mp4"),
        sleep=lambda _seconds: None,
    ).run(queue, parallel=True)

    submit_indexes = [index for index, event in enumerate(events) if event[0] == "submit"]
    poll_indexes = [index for index, event in enumerate(events) if event[0] == "poll"]
    assert len(submit_indexes) == 2
    assert len(poll_indexes) == 2
    assert max(submit_indexes) < min(poll_indexes)
