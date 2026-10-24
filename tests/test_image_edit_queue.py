from pathlib import Path

from app.wavespeed.image_edit_queue import (
    ImageEditCampaign,
    ImageEditCampaignStore,
    ImageEditQueue,
    ImageEditQueueConfig,
    ImageEditQueueItem,
    ImageEditQueueRunner,
)
from app.wavespeed.queue import QueueStatus
from app.wavespeed.schemas import WaveSpeedPrediction
from app.wavespeed.validation import QWEN_IMAGE_3_EDIT_MODEL_ID, build_qwen_image_3_edit_payload


QWEN_CONFIG = ImageEditQueueConfig("qwen_image", QWEN_IMAGE_3_EDIT_MODEL_ID,
                                   build_qwen_image_3_edit_payload, "qwen_image_campaigns.json", 3)


def test_store_recovers_running_item(tmp_path: Path):
    store = ImageEditCampaignStore(tmp_path / "qwen_image_campaigns.json")
    campaign = ImageEditCampaign(queue=ImageEditQueue(items=[ImageEditQueueItem(
        prompt="replace background", image_paths=["C:/input.png"],
        settings={"resolution": "2k", "enable_prompt_expansion": False, "seed": 7},
        status=QueueStatus.RUNNING, task_id="pred-1",
    )]))

    store.save(campaign, active=True)

    campaigns, active_id = store.load()
    assert active_id == campaign.campaign_id
    assert campaigns[0].queue.items[0].status == QueueStatus.PENDING
    assert campaigns[0].queue.items[0].task_id == "pred-1"


def test_runner_recovers_task_without_second_submit(tmp_path: Path):
    class Client:
        submitted = []
        queried = []

        def get_result(self, task_id):
            self.queried.append(task_id)
            return WaveSpeedPrediction(task_id, "completed", outputs=["https://cdn/out.png"])

        def submit(self, *_args, **_kwargs):
            self.submitted.append(True)
            raise AssertionError("Existing task must not be submitted again")

    item = ImageEditQueueItem(prompt="edit", image_paths=[str(tmp_path / "missing.png")], task_id="existing")
    queue = ImageEditQueue(items=[item], output_dir=str(tmp_path))
    client = Client()

    ImageEditQueueRunner(QWEN_CONFIG, client,
                         upload_file=lambda _path: (_ for _ in ()).throw(AssertionError("No upload")),
                         save_output=lambda *_args: "out.png").run(queue)

    assert client.submitted == []
    assert client.queried == ["existing"]
    assert item.status == QueueStatus.COMPLETED


def test_pause_leaves_next_item_pending(tmp_path: Path):
    class Client:
        def estimate_price(self, *_args, **_kwargs):
            return {"price": 0.01}

        def submit(self, payload, **_kwargs):
            return WaveSpeedPrediction(payload["prompt"], "completed", outputs=["https://cdn/out.png"])

    for name in ("one.png", "two.png"):
        (tmp_path / name).write_bytes(b"image")
    queue = ImageEditQueue(items=[
        ImageEditQueueItem(prompt="one", image_paths=[str(tmp_path / "one.png")]),
        ImageEditQueueItem(prompt="two", image_paths=[str(tmp_path / "two.png")]),
    ])
    runner = ImageEditQueueRunner(QWEN_CONFIG, Client(),
                                  upload_file=lambda path: {"download_url": f"https://cdn/{path.name}"},
                                  save_output=lambda *_args: "out.png",
                                  on_update=lambda item: setattr(queue, "paused", True)
                                  if item.status == QueueStatus.COMPLETED else None)

    runner.run(queue)

    assert queue.items[0].status == QueueStatus.COMPLETED
    assert queue.items[1].status == QueueStatus.PENDING
