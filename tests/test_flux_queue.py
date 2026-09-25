from pathlib import Path

import pytest

from app.wavespeed.flux_queue import FluxCampaign, FluxCampaignStore, FluxQueue, FluxQueueItem, FluxQueueRunner
from app.wavespeed.queue import QueueStatus
from app.wavespeed.schemas import WaveSpeedPrediction
from app.wavespeed.validation import FLUX_MODEL_ID


def test_flux_campaign_round_trip_and_recover_running_items(tmp_path: Path):
    store = FluxCampaignStore(tmp_path / "flux_campaigns.json")
    campaign = FluxCampaign(name="Ediciones", queue=FluxQueue(items=[
        FluxQueueItem(prompt="Exact prompt", image_paths=["C:/one.png", "C:/two.png"],
                      size="1024*1024", seed=42, status=QueueStatus.RUNNING, task_id="pred-1"),
    ]))
    store.save(campaign, active=True)

    campaigns, active_id = store.load()
    assert active_id == campaign.campaign_id
    assert campaigns[0].name == "Ediciones"
    item = campaigns[0].queue.items[0]
    assert item.prompt == "Exact prompt"
    assert item.image_paths == ["C:/one.png", "C:/two.png"]
    assert item.seed == 42
    assert item.status == QueueStatus.PENDING
    assert item.task_id == "pred-1"


def test_flux_campaign_store_refuses_to_overwrite_unreadable_file(tmp_path: Path):
    path = tmp_path / "flux_campaigns.json"
    path.write_text("{broken", encoding="utf-8")
    store = FluxCampaignStore(path)
    with pytest.raises(ValueError, match="cannot be read"):
        store.save(FluxCampaign())
    assert path.read_text(encoding="utf-8") == "{broken"


def test_flux_queue_runs_sequentially_and_resumes_existing_prediction(tmp_path: Path):
    events = []

    class Client:
        def estimate_price(self, payload, *, model_id):
            assert model_id == FLUX_MODEL_ID
            events.append(("estimate", payload["prompt"]))
            return {"price": 0.016}

        def submit(self, payload, *, model_id):
            assert model_id == FLUX_MODEL_ID
            events.append(("submit", payload["prompt"]))
            return WaveSpeedPrediction("new-task", "created")

        def get_result(self, task_id):
            events.append(("get", task_id))
            return WaveSpeedPrediction(task_id, "completed", outputs=["https://cdn/out.png"])

        def poll_result(self, task_id, **_kwargs):
            events.append(("poll", task_id))
            return WaveSpeedPrediction(task_id, "completed", outputs=["https://cdn/out.png"])

    first = FluxQueueItem(prompt="One", image_paths=[str(tmp_path / "one.png")])
    second = FluxQueueItem(prompt="Two", image_paths=[str(tmp_path / "two.png")], task_id="existing-task")
    for name in ("one.png", "two.png"):
        (tmp_path / name).write_bytes(b"image")
    queue = FluxQueue(items=[first, second], output_dir=str(tmp_path))
    FluxQueueRunner(Client(), upload_file=lambda path: {"download_url": f"https://cdn/{path.name}"},
                    save_output=lambda _output, _dir, task_id: f"{task_id}.png").run(queue)

    assert [event for event in events if event[0] == "submit"] == [("submit", "One")]
    assert ("get", "existing-task") in events
    assert events.index(("poll", "new-task")) < events.index(("get", "existing-task"))
    assert all(item.status == QueueStatus.COMPLETED for item in queue.items)
    assert first.output_file == "new-task.png"
    assert second.output_file == "existing-task.png"


def test_flux_queue_pause_stops_before_next_item(tmp_path: Path):
    calls = []

    class Client:
        def estimate_price(self, payload, *, model_id):
            return {"price": 0.016}

        def submit(self, payload, *, model_id):
            calls.append(payload["prompt"])
            return WaveSpeedPrediction("task-1", "completed", outputs=["https://cdn/out.png"])

    queue = FluxQueue(items=[FluxQueueItem(prompt="One", image_paths=[str(tmp_path / "one.png")]),
                             FluxQueueItem(prompt="Two", image_paths=[str(tmp_path / "two.png")])])
    for name in ("one.png", "two.png"):
        (tmp_path / name).write_bytes(b"image")
    runner = FluxQueueRunner(Client(), upload_file=lambda _path: {"download_url": "https://cdn/input.png"},
                             save_output=lambda *_args: "out.png",
                             on_update=lambda item: setattr(queue, "paused", True) if item.status == QueueStatus.COMPLETED else None)
    runner.run(queue)

    assert calls == ["One"]
    assert queue.items[1].status == QueueStatus.PENDING


def test_flux_queue_resumes_task_without_reuploading_missing_input(tmp_path: Path):
    calls = []

    class Client:
        def get_result(self, task_id):
            calls.append(("get", task_id))
            return WaveSpeedPrediction(task_id, "completed", outputs=["https://cdn/result.png"])

        def submit(self, *_args, **_kwargs):
            raise AssertionError("Existing task must never be resubmitted")

    item = FluxQueueItem(prompt="Original", image_paths=[str(tmp_path / "missing.png")], task_id="existing")
    queue = FluxQueue(items=[item])
    FluxQueueRunner(Client(), upload_file=lambda _path: (_ for _ in ()).throw(AssertionError("No upload")),
                    save_output=lambda *_args: "result.png").run(queue)

    assert calls == [("get", "existing")]
    assert item.status == QueueStatus.COMPLETED
    assert item.output_file == "result.png"


def test_flux_queue_parallel_submits_multiple_items(tmp_path: Path):
    calls = []

    class Client:
        def estimate_price(self, *_args, **_kwargs):
            return {"price": 0.016}

        def submit(self, payload, *, model_id):
            calls.append((payload["prompt"], model_id))
            return WaveSpeedPrediction(payload["prompt"], "completed", outputs=["https://cdn/result.png"])

    for name in ("a.png", "b.png"):
        (tmp_path / name).write_bytes(b"image")
    queue = FluxQueue(execution_mode="parallel", items=[
        FluxQueueItem(prompt="A", image_paths=[str(tmp_path / "a.png")]),
        FluxQueueItem(prompt="B", image_paths=[str(tmp_path / "b.png")]),
    ])
    FluxQueueRunner(Client(), upload_file=lambda path: {"download_url": f"https://cdn/{path.name}"},
                    save_output=lambda *_args: "result.png").run(queue)

    assert sorted(calls) == [("A", FLUX_MODEL_ID), ("B", FLUX_MODEL_ID)]
    assert all(item.status == QueueStatus.COMPLETED for item in queue.items)
