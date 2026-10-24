from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from app.utils.paths import data_dir
from app.wavespeed.queue import QueueStatus
from app.wavespeed.validation import TERMINAL_STATUSES, validate_local_reference_file


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ImageEditQueueConfig:
    key: str
    model_id: str
    payload_builder: Callable[..., dict[str, Any]]
    campaign_filename: str
    max_images: int


@dataclass
class ImageEditQueueItem:
    item_id: str = field(default_factory=lambda: uuid4().hex)
    image_paths: list[str] = field(default_factory=list)
    prompt: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    status: str = QueueStatus.PENDING
    task_id: str = ""
    price: float | None = None
    output_file: str = ""
    error: str = ""
    created_at: str = field(default_factory=_now)

    def retry(self) -> None:
        self.status, self.task_id, self.price, self.output_file, self.error = (
            QueueStatus.PENDING, "", None, "", ""
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ImageEditQueueItem":
        return cls(**{name: value[name] for name in cls.__dataclass_fields__ if name in value})


@dataclass
class ImageEditQueue:
    items: list[ImageEditQueueItem] = field(default_factory=list)
    output_dir: str = ""
    paused: bool = False
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {"items": [item.to_dict() for item in self.items], "output_dir": self.output_dir,
                "paused": self.paused, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ImageEditQueue":
        items = [ImageEditQueueItem.from_dict(item) for item in value.get("items", []) if isinstance(item, dict)]
        for item in items:
            if item.status in {QueueStatus.RUNNING, QueueStatus.PAUSED}:
                item.status = QueueStatus.PENDING
        return cls(items=items, output_dir=str(value.get("output_dir") or ""),
                   paused=bool(value.get("paused", False)), updated_at=str(value.get("updated_at") or _now()))


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
            urls.append(self.upload_file(path)["download_url"])
        return self.config.payload_builder(prompt=item.prompt, images=urls, **item.settings)

    def _process(self, item: ImageEditQueueItem, queue: ImageEditQueue) -> None:
        try:
            item.status, item.error = QueueStatus.RUNNING, ""
            self._notify(item)
            if item.task_id:
                prediction = self.client.get_result(item.task_id)
            else:
                payload = self._payload(item)
                try:
                    price = self.client.estimate_price(payload, model_id=self.config.model_id)
                    item.price = float(price.get("discounted_price", price.get("price")))
                except Exception:
                    item.price = None
                self._notify(item)
                prediction = self.client.submit(payload, model_id=self.config.model_id)
                item.task_id = prediction.id
                self._notify(item)
            result = prediction if prediction.status in TERMINAL_STATUSES else self.client.poll_result(prediction.id)
            if result.status == "completed" and result.outputs:
                item.output_file = self.save_output(result.outputs[0], queue.output_dir, result.id)
                item.status = QueueStatus.COMPLETED
            elif result.status == "completed":
                raise ValueError("Qwen completed without an output image.")
            else:
                item.status, item.error = QueueStatus.FAILED, result.error or f"WaveSpeed task ended with status: {result.status}"
        except Exception as exc:
            item.status, item.error = QueueStatus.FAILED, str(exc)
        self._notify(item)

    def run(self, queue: ImageEditQueue) -> ImageEditQueue:
        for item in queue.items:
            if queue.paused:
                break
            if item.status in {QueueStatus.PENDING, QueueStatus.PAUSED}:
                self._process(item, queue)
        return queue

    def estimate_prices(self, queue: ImageEditQueue) -> float:
        total = 0.0
        for item in queue.items:
            try:
                payload = self._payload(item)
                price = self.client.estimate_price(payload, model_id=self.config.model_id)
                item.price, item.error = float(price.get("discounted_price", price.get("price"))), ""
                total += item.price
            except Exception as exc:
                item.price, item.error = None, f"No se pudo estimar: {exc}"
            self._notify(item)
        return round(total, 8)
