from __future__ import annotations

import json
import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from app.utils.paths import data_dir
from app.wavespeed.flux_queue import FluxCampaign, FluxCampaignStore, FluxQueueItem
from app.wavespeed.queue import QueueStatus
from app.wavespeed.validation import (
    FLUX_MODEL_ID,
    NANO_BANANA_PRO_MODEL_ID,
    QWEN_IMAGE_3_EDIT_MODEL_ID,
    TERMINAL_STATUSES,
    build_flux_payload,
    build_nano_banana_pro_payload,
    build_qwen_image_3_edit_payload,
    validate_local_reference_file,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ImageEditQueueConfig:
    key: str
    model_id: str
    payload_builder: Callable[..., dict[str, Any]]
    campaign_filename: str
    max_images: int = 0
    display_name: str = "Image edit"
    output_suffix: str = ".png"
    max_parallelism: int = 4
    group_references: bool = False


FLUX_IMAGE_EDIT_CONFIG = ImageEditQueueConfig(
    "flux", FLUX_MODEL_ID, build_flux_payload, "flux_image_edit_campaigns.json", 3,
    "Flux", ".png", 4,
)
QWEN_IMAGE_EDIT_CONFIG = ImageEditQueueConfig(
    "qwen_image", QWEN_IMAGE_3_EDIT_MODEL_ID, build_qwen_image_3_edit_payload,
    "qwen_image_campaigns.json", 3, "Qwen Image 3.0 Edit", ".png", 4, True,
)
NANO_BANANA_IMAGE_EDIT_CONFIG = ImageEditQueueConfig(
    "nano_banana", NANO_BANANA_PRO_MODEL_ID, build_nano_banana_pro_payload,
    "nano_banana_campaigns.json", 14, "Nano Banana Pro", ".png", 4,
)


@dataclass
class ImageEditQueueItem:
    item_id: str = field(default_factory=lambda: uuid4().hex)
    image_paths: list[str] = field(default_factory=list)
    prompt: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    model_key: str = ""
    model_id: str = ""
    repetition: int = 1
    copies: int = 1
    status: str = QueueStatus.PENDING
    task_id: str = ""
    copy_task_ids: list[str] = field(default_factory=list)
    copy_statuses: list[str] = field(default_factory=list)
    copy_prices: list[float | None] = field(default_factory=list)
    copy_output_files: list[str] = field(default_factory=list)
    copy_errors: list[str] = field(default_factory=list)
    price: float | None = None
    output_file: str = ""
    error: str = ""
    created_at: str = field(default_factory=_now)

    def retry(self) -> None:
        if self.copies > 1 and self.copy_statuses:
            for index, status in enumerate(self.copy_statuses):
                if status == QueueStatus.FAILED:
                    self.copy_statuses[index] = QueueStatus.PENDING
                    self.copy_task_ids[index] = ""
                    self.copy_prices[index] = None
                    self.copy_errors[index] = ""
            self._sync_copy_summary()
            self.status, self.error = QueueStatus.PENDING, ""
            return
        self.status, self.task_id, self.price, self.output_file, self.error = QueueStatus.PENDING, "", None, "", ""

    def _sync_copy_summary(self) -> None:
        self.task_id = "\n".join(task_id for task_id in self.copy_task_ids if task_id)
        self.output_file = "\n".join(path for path in self.copy_output_files if path)
        failed_errors = [error for status, error in zip(self.copy_statuses, self.copy_errors)
                         if status == QueueStatus.FAILED and error]
        self.error = "\n".join(failed_errors)
        self.price = (sum(price for price in self.copy_prices if price is not None)
                      if all(price is not None for price in self.copy_prices) else None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ImageEditQueueItem":
        item = cls(**{name: value[name] for name in cls.__dataclass_fields__ if name in value})
        item.copy_statuses = [
            QueueStatus.PENDING if status in {QueueStatus.RUNNING, QueueStatus.PAUSED} else status
            for status in item.copy_statuses
        ]
        return item


@dataclass
class ImageEditQueue:
    items: list[ImageEditQueueItem] = field(default_factory=list)
    output_dir: str = ""
    concurrency: int = 1
    paused: bool = False
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {"items": [item.to_dict() for item in self.items], "output_dir": self.output_dir,
                "concurrency": self.concurrency,
                "paused": self.paused, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ImageEditQueue":
        items = [ImageEditQueueItem.from_dict(item) for item in value.get("items", []) if isinstance(item, dict)]
        for item in items:
            if item.status in {QueueStatus.RUNNING, QueueStatus.PAUSED}:
                item.status = QueueStatus.PENDING
        try:
            concurrency = int(value.get("concurrency", 1))
        except (TypeError, ValueError):
            concurrency = 1
        return cls(items=items, output_dir=str(value.get("output_dir") or ""), concurrency=max(1, concurrency),
                   paused=bool(value.get("paused", False)), updated_at=str(value.get("updated_at") or _now()))


def expand_image_edit_batch(
    config: ImageEditQueueConfig,
    image_paths: list[str],
    *,
    repetitions: int,
    copies: int = 1,
    prompt: str,
    settings: dict[str, Any],
) -> list[ImageEditQueueItem]:
    if repetitions < 1:
        raise ValueError("Las repeticiones deben ser al menos 1.")
    prepared = [str(Path(path).resolve()) for path in image_paths]
    if not prepared:
        raise ValueError("Agregá al menos una imagen.")
    for path in prepared:
        validate_local_reference_file(Path(path), "reference_images")
    if config.group_references:
        if copies < 1:
            raise ValueError("Las copias deben ser al menos 1.")
        config.payload_builder(prompt=prompt, images=prepared, **settings)
        return [ImageEditQueueItem(
            image_paths=prepared, prompt=prompt, settings=dict(settings),
            model_key=config.key, model_id=config.model_id, copies=copies,
        )]
    for path in prepared:
        config.payload_builder(prompt=prompt, images=[path], **settings)
    return [
        ImageEditQueueItem(
            image_paths=[path], prompt=prompt, settings=dict(settings),
            model_key=config.key, model_id=config.model_id, repetition=repetition,
        )
        for path in prepared for repetition in range(1, repetitions + 1)
    ]


@dataclass
class ImageEditCampaign:
    campaign_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = "Nueva campaña Qwen"
    queue: ImageEditQueue = field(default_factory=ImageEditQueue)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {"campaign_id": self.campaign_id, "name": self.name, "queue": self.queue.to_dict(),
                "created_at": self.created_at, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ImageEditCampaign":
        return cls(campaign_id=str(value.get("campaign_id") or uuid4().hex),
                   name=str(value.get("name") or "Nueva campaña Qwen"),
                   queue=ImageEditQueue.from_dict(value.get("queue") or {}),
                   created_at=str(value.get("created_at") or _now()),
                   updated_at=str(value.get("updated_at") or _now()))


def _legacy_flux_item(item: FluxQueueItem) -> ImageEditQueueItem:
    return ImageEditQueueItem(
        item_id=item.item_id,
        image_paths=list(item.image_paths),
        prompt=item.prompt,
        settings={
            "size": item.size,
            "seed": item.seed,
            "enable_sync_mode": item.enable_sync_mode,
            "enable_base64_output": item.enable_base64_output,
        },
        model_key=FLUX_IMAGE_EDIT_CONFIG.key,
        model_id=FLUX_IMAGE_EDIT_CONFIG.model_id,
        repetition=1,
        status=item.status,
        task_id=item.task_id,
        price=item.price,
        output_file=item.output_file,
        error=item.error,
        created_at=item.created_at,
    )


def load_legacy_flux_campaigns(store: FluxCampaignStore) -> tuple[list[ImageEditCampaign], str | None]:
    campaigns, active_id = store.load()
    converted = [
        ImageEditCampaign(
            campaign_id=campaign.campaign_id,
            name=campaign.name,
            queue=ImageEditQueue(
                items=[_legacy_flux_item(item) for item in campaign.queue.items],
                output_dir=campaign.queue.output_dir,
                concurrency=4 if campaign.queue.execution_mode == "parallel" else 1,
                paused=campaign.queue.paused,
                updated_at=campaign.queue.updated_at,
            ),
            created_at=campaign.created_at,
            updated_at=campaign.updated_at,
        )
        for campaign in campaigns
    ]
    return converted, active_id


class ImageEditCampaignStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or data_dir() / "qwen_image_campaigns.json")
        self._lock = Lock()

    def load(self) -> tuple[list[ImageEditCampaign], str | None]:
        if not self.path.exists():
            return [], None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Image-edit campaign file cannot be read: {self.path}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("campaigns"), list):
            raise ValueError(f"Image-edit campaign file cannot be read: {self.path}")
        return ([ImageEditCampaign.from_dict(item) for item in value["campaigns"] if isinstance(item, dict)],
                str(value["active_id"]) if value.get("active_id") else None)

    def _write(self, campaigns: list[ImageEditCampaign], active_id: str | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps({"active_id": active_id, "campaigns": [c.to_dict() for c in campaigns]},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def save(self, campaign: ImageEditCampaign, *, active: bool = False) -> None:
        with self._lock:
            campaigns, active_id = self.load()
            campaign.updated_at = _now()
            for index, old in enumerate(campaigns):
                if old.campaign_id == campaign.campaign_id:
                    campaigns[index] = campaign
                    break
            else:
                campaigns.insert(0, campaign)
            self._write(campaigns, campaign.campaign_id if active else active_id)

    def delete(self, campaign_id: str) -> None:
        with self._lock:
            campaigns, active_id = self.load()
            campaigns = [item for item in campaigns if item.campaign_id != campaign_id]
            self._write(campaigns, campaigns[0].campaign_id if active_id == campaign_id and campaigns else active_id)


class ImageEditQueueRunner:
    def __init__(self, config: ImageEditQueueConfig, client, *, upload_file: Callable[[Path], dict[str, Any]],
                 save_output: Callable[[object, str, str], str],
                 on_update: Callable[[ImageEditQueueItem], None] | None = None) -> None:
        self.config, self.client, self.upload_file, self.save_output, self.on_update = (
            config, client, upload_file, save_output, on_update)
        self._uploads: dict[str, str] = {}
        self._upload_lock = Lock()

    def _notify(self, item: ImageEditQueueItem) -> None:
        if self.on_update:
            self.on_update(item)

    def _payload(self, item: ImageEditQueueItem) -> dict[str, Any]:
        local = {"prompt": item.prompt, "images": item.image_paths, **item.settings}
        self.config.payload_builder(**local)
        urls = []
        for name in item.image_paths:
            path = Path(name)
            validate_local_reference_file(path, "reference_images")
            key = str(path.resolve())
            with self._upload_lock:
                if key not in self._uploads:
                    self._uploads[key] = self.upload_file(path)["download_url"]
                urls.append(self._uploads[key])
        return self.config.payload_builder(prompt=item.prompt, images=urls, **item.settings)

    def _process(self, item: ImageEditQueueItem, queue: ImageEditQueue) -> None:
        if item.copies > 1:
            self._process_copies(item, queue)
            return
        self._process_single(item, queue, self._notify)

    def _process_single(self, item: ImageEditQueueItem, queue: ImageEditQueue,
                        notify: Callable[[ImageEditQueueItem], None]) -> None:
        try:
            item.status, item.error = QueueStatus.RUNNING, ""
            notify(item)
            if item.task_id:
                prediction = self.client.get_result(item.task_id)
            else:
                payload = self._payload(item)
                try:
                    price = self.client.estimate_price(payload, model_id=self.config.model_id)
                    item.price = float(price.get("discounted_price", price.get("price")))
                except Exception:
                    item.price = None
                notify(item)
                prediction = self.client.submit(payload, model_id=self.config.model_id)
                item.task_id = prediction.id
                notify(item)
            result = prediction if prediction.status in TERMINAL_STATUSES else self.client.poll_result(prediction.id)
            if result.status == "completed" and result.outputs:
                item.output_file = self.save_output(result.outputs[0], queue.output_dir, result.id)
                item.status = QueueStatus.COMPLETED
            elif result.status == "completed":
                raise ValueError(f"{self.config.display_name} completed without an output image.")
            else:
                item.status, item.error = QueueStatus.FAILED, result.error or f"WaveSpeed task ended with status: {result.status}"
        except Exception as exc:
            item.status, item.error = QueueStatus.FAILED, str(exc)
        notify(item)

    def _process_copies(self, item: ImageEditQueueItem, queue: ImageEditQueue) -> None:
        count = item.copies
        for values, default in (
            (item.copy_task_ids, ""),
            (item.copy_statuses, QueueStatus.PENDING),
            (item.copy_prices, None),
            (item.copy_output_files, ""),
            (item.copy_errors, ""),
        ):
            values.extend([default] * (count - len(values)))
            del values[count:]

        update_lock = Lock()

        def process_copy(index: int) -> None:
            if item.copy_statuses[index] == QueueStatus.COMPLETED:
                return
            child = ImageEditQueueItem(
                image_paths=list(item.image_paths), prompt=item.prompt, settings=dict(item.settings),
                model_key=item.model_key, model_id=item.model_id, task_id=item.copy_task_ids[index],
                status=item.copy_statuses[index], price=item.copy_prices[index],
                output_file=item.copy_output_files[index], error=item.copy_errors[index],
            )

            def update(updated: ImageEditQueueItem) -> None:
                with update_lock:
                    item.copy_task_ids[index] = updated.task_id
                    item.copy_statuses[index] = updated.status
                    item.copy_prices[index] = updated.price
                    item.copy_output_files[index] = updated.output_file
                    item.copy_errors[index] = updated.error
                    item._sync_copy_summary()
                    if all(status == QueueStatus.COMPLETED for status in item.copy_statuses):
                        item.status = QueueStatus.COMPLETED
                    elif any(status == QueueStatus.FAILED for status in item.copy_statuses) and all(
                        status in {QueueStatus.COMPLETED, QueueStatus.FAILED} for status in item.copy_statuses
                    ):
                        item.status = QueueStatus.FAILED
                    else:
                        item.status = QueueStatus.RUNNING
                    self._notify(item)

            self._process_single(child, queue, update)

        pending = [index for index, status in enumerate(item.copy_statuses)
                   if status != QueueStatus.COMPLETED]
        item.status = QueueStatus.RUNNING
        self._notify(item)
        worker_count = min(max(1, self.config.max_parallelism), len(pending))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            pending_copies = iter(pending)
            futures = set()
            while len(futures) < worker_count:
                try:
                    futures.add(executor.submit(process_copy, next(pending_copies)))
                except StopIteration:
                    break
            while futures:
                completed, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in completed:
                    future.result()
                while len(futures) < worker_count:
                    try:
                        futures.add(executor.submit(process_copy, next(pending_copies)))
                    except StopIteration:
                        break
        if all(status == QueueStatus.COMPLETED for status in item.copy_statuses):
            item.status = QueueStatus.COMPLETED
        elif any(status == QueueStatus.FAILED for status in item.copy_statuses):
            item.status = QueueStatus.FAILED
        self._notify(item)

    def run(self, queue: ImageEditQueue) -> ImageEditQueue:
        pending = iter(item for item in queue.items if item.status in {QueueStatus.PENDING, QueueStatus.PAUSED})
        if queue.paused:
            return queue
        worker_count = (1 if self.config.group_references else
                        min(max(1, queue.concurrency), max(1, self.config.max_parallelism),
                            sum(1 for item in queue.items if item.status in {QueueStatus.PENDING, QueueStatus.PAUSED})))
        if worker_count < 1:
            return queue
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = set()
            while not queue.paused and len(futures) < worker_count:
                try:
                    futures.add(executor.submit(self._process, next(pending), queue))
                except StopIteration:
                    break
            while futures:
                completed, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in completed:
                    future.result()
                while not queue.paused and len(futures) < worker_count:
                    try:
                        futures.add(executor.submit(self._process, next(pending), queue))
                    except StopIteration:
                        break
        return queue

    def estimate_prices(self, queue: ImageEditQueue) -> float:
        total = 0.0
        for item in queue.items:
            try:
                payload = self._payload(item)
                price = self.client.estimate_price(payload, model_id=self.config.model_id)
                item.price = float(price.get("discounted_price", price.get("price"))) * item.copies
                item.error = ""
                total += item.price
            except Exception as exc:
                item.price, item.error = None, f"No se pudo estimar: {exc}"
            self._notify(item)
        return round(total, 8)
