import threading
from pathlib import Path

from app.wavespeed.image_edit_queue import (
    FLUX_IMAGE_EDIT_CONFIG,
    ImageEditCampaign,
    ImageEditCampaignStore,
    ImageEditQueue,
    ImageEditQueueConfig,
    ImageEditQueueItem,
    ImageEditQueueRunner,
    NANO_BANANA_IMAGE_EDIT_CONFIG,
    QWEN_IMAGE_EDIT_CONFIG,
    expand_image_edit_batch,
    load_legacy_flux_campaigns,
)
from app.wavespeed.flux_queue import FluxCampaign, FluxCampaignStore, FluxQueue, FluxQueueItem
from app.wavespeed.queue import QueueStatus
from app.wavespeed.schemas import WaveSpeedPrediction
from app.wavespeed.validation import (
    FLUX_MODEL_ID,
    NANO_BANANA_PRO_MODEL_ID,
    QWEN_IMAGE_3_EDIT_MODEL_ID,
    build_qwen_image_3_edit_payload,
)


QWEN_CONFIG = ImageEditQueueConfig("qwen_image", QWEN_IMAGE_3_EDIT_MODEL_ID,
                                   build_qwen_image_3_edit_payload, "qwen_image_campaigns.json", 3,
                                   group_references=True)


def test_expand_batch_creates_one_job_for_reference_set_and_requested_copies(tmp_path: Path):
    sources = []
    for index in range(3):
        source = tmp_path / f"source-{index}.png"
        source.write_bytes(b"image")
        sources.append(str(source))

    items = expand_image_edit_batch(
        QWEN_CONFIG, sources, repetitions=1, copies=7, prompt="replace background",
        settings={"resolution": "1k", "aspect_ratio": "", "enable_prompt_expansion": True, "seed": -1},
    )

    assert len(items) == 1
    assert items[0].copies == 7
    assert items[0].image_paths == [str(Path(source).resolve()) for source in sources]
    assert {item.model_id for item in items} == {QWEN_IMAGE_3_EDIT_MODEL_ID}
    assert len({item.item_id for item in items}) == 1


def test_campaign_round_trip_preserves_requested_concurrency_and_task_model(tmp_path: Path):
    store = ImageEditCampaignStore(tmp_path / "campaigns.json")
    queue = ImageEditQueue(concurrency=3, items=[ImageEditQueueItem(
        image_paths=["C:/input.png"], prompt="edit", model_key="qwen_image",
        model_id=QWEN_IMAGE_3_EDIT_MODEL_ID, repetition=2, copies=7,
        settings={"resolution": "2k", "aspect_ratio": "", "enable_prompt_expansion": True, "seed": -1},
    )])
    campaign = ImageEditCampaign(name="Doce resultados", queue=queue)

    store.save(campaign, active=True)
    loaded, active_id = store.load()

    assert active_id == campaign.campaign_id
    assert loaded[0].queue.concurrency == 3
    assert loaded[0].queue.items[0].repetition == 2
    assert loaded[0].queue.items[0].copies == 7
    assert loaded[0].queue.items[0].model_id == QWEN_IMAGE_3_EDIT_MODEL_ID


def test_runner_submits_all_copies_of_the_reference_set_in_parallel(tmp_path: Path):
    class Client:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.submitted = []
            self.lock = threading.Lock()
            self.gate = threading.Event()

        def estimate_price(self, *_args, **_kwargs):
            return {"price": 0.01}

        def submit(self, payload, **_kwargs):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.submitted.append(payload["images"])
                if self.active == 4:
                    self.gate.set()
                task_id = f"task-{len(self.submitted)}"
            return WaveSpeedPrediction(task_id, "created")

        def poll_result(self, task_id):
            assert self.gate.wait(1)
            with self.lock:
                self.active -= 1
            return WaveSpeedPrediction(task_id, "completed", outputs=["https://cdn/out.png"])

    sources = []
    for index in range(3):
        source = tmp_path / f"source-{index}.png"
        source.write_bytes(b"image")
        sources.append(str(source))
    queue = ImageEditQueue(concurrency=3, items=expand_image_edit_batch(
        QWEN_CONFIG, sources, repetitions=1, copies=12, prompt="edit",
        settings={"resolution": "1k", "aspect_ratio": "", "enable_prompt_expansion": True, "seed": -1},
    ))
    config = ImageEditQueueConfig("qwen_image", QWEN_IMAGE_3_EDIT_MODEL_ID,
                                  build_qwen_image_3_edit_payload, "qwen_image_campaigns.json",
                                  max_parallelism=4, group_references=True)
    client = Client()

    ImageEditQueueRunner(config, client,
                         upload_file=lambda path: {"download_url": f"https://cdn/{path.name}"},
                         save_output=lambda *_args: "out.png").run(queue)

    assert len(client.submitted) == 12
    assert all(images == [f"https://cdn/source-{index}.png" for index in range(3)]
               for images in client.submitted)
    assert client.max_active == 4
    assert {item.status for item in queue.items} == {QueueStatus.COMPLETED}
    assert len(queue.items[0].copy_output_files) == 12
    assert queue.items[0].price == 0.12


def test_runner_continues_after_one_failure_and_retries_only_that_item(tmp_path: Path):
    class Client:
        def __init__(self):
            self.submitted = []
            self.failed_once = False

        def estimate_price(self, *_args, **_kwargs):
            return {"price": 0.01}

        def submit(self, payload, **_kwargs):
            self.submitted.append(payload["images"])
            if not self.failed_once:
                self.failed_once = True
                raise RuntimeError("planned submit failure")
            return WaveSpeedPrediction(str(len(self.submitted)), "completed", outputs=["https://cdn/out.png"])

    sources = []
    for index in range(2):
        source = tmp_path / f"source-{index}.png"
        source.write_bytes(b"image")
        sources.append(str(source))
    queue = ImageEditQueue(items=expand_image_edit_batch(
        QWEN_CONFIG, sources, repetitions=1, copies=2, prompt="edit",
        settings={"resolution": "1k", "aspect_ratio": "", "enable_prompt_expansion": True, "seed": -1},
    ))
    client = Client()
    runner = ImageEditQueueRunner(QWEN_CONFIG, client,
                                  upload_file=lambda path: {"download_url": f"https://cdn/{path.name}"},
                                  save_output=lambda *_args: "out.png")

    runner.run(queue)

    failed = [item for item in queue.items if item.status == QueueStatus.FAILED]
    assert len(failed) == 1
    assert sum(item.status == QueueStatus.COMPLETED for item in queue.items) == 0
    assert failed[0].copy_statuses.count(QueueStatus.COMPLETED) == 1
    failed[0].retry()
    runner.run(queue)

    assert {item.status for item in queue.items} == {QueueStatus.COMPLETED}
    assert len(client.submitted) == 3
    assert all(len(images) == 2 for images in client.submitted)


def test_parallel_repetitions_upload_a_source_once(tmp_path: Path):
    class Client:
        def estimate_price(self, *_args, **_kwargs):
            return {"price": 0.01}

        def submit(self, payload, **_kwargs):
            return WaveSpeedPrediction(str(len(payload["images"])), "completed", outputs=["https://cdn/out.png"])

    source = tmp_path / "same.png"
    source.write_bytes(b"image")
    queue = ImageEditQueue(concurrency=4, items=expand_image_edit_batch(
        QWEN_CONFIG, [str(source)], repetitions=1, copies=6, prompt="edit",
        settings={"resolution": "1k", "aspect_ratio": "", "enable_prompt_expansion": True, "seed": -1},
    ))
    uploads = []

    ImageEditQueueRunner(QWEN_CONFIG, Client(),
                         upload_file=lambda path: uploads.append(path) or {"download_url": "https://cdn/same.png"},
                         save_output=lambda *_args: "out.png").run(queue)

    assert uploads == [source]


def test_image_edit_adapters_build_only_compatible_payloads():
    flux = FLUX_IMAGE_EDIT_CONFIG.payload_builder(
        prompt="edit", images=["https://cdn/source.png"], size="1024*1024", seed=7,
        enable_sync_mode=False, enable_base64_output=False,
    )
    nano = NANO_BANANA_IMAGE_EDIT_CONFIG.payload_builder(
        prompt="edit", images=["https://cdn/source.png"], aspect_ratio="4:5", resolution="4k",
        output_format="jpeg", enable_sync_mode=False, enable_base64_output=False,
    )
    qwen = QWEN_IMAGE_EDIT_CONFIG.payload_builder(
        prompt="edit", images=["https://cdn/source.png"], aspect_ratio="9:16", resolution="2k",
        enable_prompt_expansion=False, seed=42,
    )

    assert FLUX_IMAGE_EDIT_CONFIG.model_id == FLUX_MODEL_ID
    assert flux["size"] == "1024*1024"
    assert NANO_BANANA_IMAGE_EDIT_CONFIG.model_id == NANO_BANANA_PRO_MODEL_ID
    assert nano["output_format"] == "jpeg"
    assert QWEN_IMAGE_EDIT_CONFIG.model_id == QWEN_IMAGE_3_EDIT_MODEL_ID
    assert "size" not in qwen
    assert "output_format" not in qwen


def test_legacy_flux_campaign_conversion_does_not_mutate_source_file(tmp_path: Path):
    legacy_path = tmp_path / "flux_campaigns.json"
    store = FluxCampaignStore(legacy_path)
    store.save(FluxCampaign(name="Anterior", queue=FluxQueue(items=[FluxQueueItem(
        prompt="edit", image_paths=["C:/old.png"], size="1024*1024", seed=9,
    )])), active=True)
    before = legacy_path.read_bytes()

    campaigns, active_id = load_legacy_flux_campaigns(store)

    assert active_id == campaigns[0].campaign_id
    item = campaigns[0].queue.items[0]
    assert item.model_id == FLUX_MODEL_ID
    assert item.settings["size"] == "1024*1024"
    assert legacy_path.read_bytes() == before


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
