from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from app.utils.paths import data_dir
from app.wavespeed.queue import QueueStatus
from app.wavespeed.validation import FLUX_MODEL_ID, TERMINAL_STATUSES, build_flux_payload, validate_local_reference_file


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FluxQueueItem:
    item_id: str = field(default_factory=lambda: uuid4().hex)
    prompt: str = ""
    image_paths: list[str] = field(default_factory=list)
    size: str = ""
    seed: int = -1
    enable_sync_mode: bool = False
    enable_base64_output: bool = False
    status: str = QueueStatus.PENDING
    task_id: str = ""
    price: float | None = None
    output_file: str = ""
    error: str = ""
    created_at: str = field(default_factory=_now)

    def retry(self) -> None:
        self.status = QueueStatus.PENDING
        self.task_id = ""
        self.price = None
        self.output_file = ""
        self.error = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FluxQueueItem":
        fields = cls.__dataclass_fields__
        return cls(**{key: value[key] for key in fields if key in value})


@dataclass
class FluxQueue:
    items: list[FluxQueueItem] = field(default_factory=list)
    output_dir: str = ""
    execution_mode: str = "sequential"
    paused: bool = False
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {"items": [item.to_dict() for item in self.items], "output_dir": self.output_dir,
                "execution_mode": self.execution_mode, "paused": self.paused, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FluxQueue":
        items = [FluxQueueItem.from_dict(row) for row in value.get("items", []) if isinstance(row, dict)]
        for item in items:
            if item.status in {QueueStatus.RUNNING, QueueStatus.PAUSED}:
                item.status = QueueStatus.PENDING
        return cls(items=items, output_dir=str(value.get("output_dir") or ""),
                   execution_mode=str(value.get("execution_mode") or "sequential"),
                   paused=bool(value.get("paused", False)), updated_at=str(value.get("updated_at") or _now()))


@dataclass
class FluxCampaign:
    campaign_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = "Nueva campaña Flux"
    queue: FluxQueue = field(default_factory=FluxQueue)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {"campaign_id": self.campaign_id, "name": self.name, "queue": self.queue.to_dict(),
                "created_at": self.created_at, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FluxCampaign":
        return cls(campaign_id=str(value.get("campaign_id") or uuid4().hex),
                   name=str(value.get("name") or "Nueva campaña Flux"),
                   queue=FluxQueue.from_dict(value.get("queue") or {}),
                   created_at=str(value.get("created_at") or _now()),
                   updated_at=str(value.get("updated_at") or _now()))


class FluxCampaignStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or data_dir() / "flux_campaigns.json")
        self._lock = Lock()

    def load(self) -> tuple[list[FluxCampaign], str | None]:
        if not self.path.exists():
            return [], None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Flux campaign file cannot be read: {self.path}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("campaigns"), list):
            raise ValueError(f"Flux campaign file cannot be read: {self.path}")
        campaigns = [FluxCampaign.from_dict(row) for row in value.get("campaigns", []) if isinstance(row, dict)]
        return campaigns, str(value.get("active_id")) if value.get("active_id") else None

    def _write(self, campaigns: list[FluxCampaign], active_id: str | None) -> None:
        payload = {"active_id": active_id, "campaigns": [campaign.to_dict() for campaign in campaigns]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def save(self, campaign: FluxCampaign, *, active: bool = False) -> None:
        with self._lock:
            campaigns, active_id = self.load()
            campaign.updated_at = _now()
            for index, existing in enumerate(campaigns):
                if existing.campaign_id == campaign.campaign_id:
                    campaigns[index] = campaign
                    break
            else:
                campaigns.insert(0, campaign)
            self._write(campaigns, campaign.campaign_id if active else active_id)

    def delete(self, campaign_id: str) -> None:
        with self._lock:
            campaigns, active_id = self.load()
            campaigns = [campaign for campaign in campaigns if campaign.campaign_id != campaign_id]
            if active_id == campaign_id:
                active_id = campaigns[0].campaign_id if campaigns else None
            self._write(campaigns, active_id)


class FluxQueueRunner:
    def __init__(self, client, *, upload_file: Callable[[Path], dict[str, Any]],
                 save_output: Callable[[object, str, str], str],
                 on_update: Callable[[FluxQueueItem], None] | None = None) -> None:
        self.client = client
        self.upload_file = upload_file
        self.save_output = save_output
        self.on_update = on_update
        self._uploads: dict[str, str] = {}
        self._upload_lock = Lock()

    def _notify(self, item: FluxQueueItem) -> None:
        if self.on_update:
            self.on_update(item)

    def _payload(self, item: FluxQueueItem) -> dict[str, Any]:
        # Validate the complete local snapshot before making any network request.
        build_flux_payload(prompt=item.prompt, images=item.image_paths, size=item.size, seed=item.seed,
                           enable_sync_mode=item.enable_sync_mode, enable_base64_output=item.enable_base64_output)
        urls = []
        for name in item.image_paths:
            path = Path(name)
            validate_local_reference_file(path, "reference_images")
            key = str(path.resolve())
            with self._upload_lock:
                if key not in self._uploads:
                    self._uploads[key] = self.upload_file(path)["download_url"]
                urls.append(self._uploads[key])
        return build_flux_payload(prompt=item.prompt, images=urls, size=item.size, seed=item.seed,
                                  enable_sync_mode=item.enable_sync_mode,
                                  enable_base64_output=item.enable_base64_output)

    def _process(self, item: FluxQueueItem, queue: FluxQueue, cancel_event=None) -> None:
        try:
            item.status = QueueStatus.RUNNING
            item.error = ""
            self._notify(item)
            if item.task_id:
                prediction = self.client.get_result(item.task_id)
            else:
                payload = self._payload(item)
                try:
                    price = self.client.estimate_price(payload, model_id=FLUX_MODEL_ID)
                    item.price = float(price.get("discounted_price", price.get("price")))
                except Exception:
                    item.price = None
                self._notify(item)
                prediction = self.client.submit(payload, model_id=FLUX_MODEL_ID)
                item.task_id = prediction.id
                self._notify(item)
            result = prediction if prediction.status in TERMINAL_STATUSES else self.client.poll_result(
                prediction.id, cancel_event=cancel_event)
            if result.status == "completed":
                if not result.outputs:
                    raise ValueError("Flux completed without an output image.")
                item.output_file = self.save_output(result.outputs[0], queue.output_dir, result.id)
                item.status = QueueStatus.COMPLETED
            else:
                item.status = QueueStatus.FAILED
                item.error = result.error or f"WaveSpeed task ended with status: {result.status}"
        except Exception as exc:
            item.status = QueueStatus.PAUSED if cancel_event is not None and cancel_event.is_set() else QueueStatus.FAILED
            item.error = str(exc)
        self._notify(item)

    def run(self, queue: FluxQueue, cancel_event=None) -> FluxQueue:
        pending = [item for item in queue.items if item.status in {QueueStatus.PENDING, QueueStatus.PAUSED}]
        if queue.execution_mode == "parallel":
            if queue.paused or (cancel_event is not None and cancel_event.is_set()):
                return queue
            with ThreadPoolExecutor(max_workers=min(4, max(1, len(pending)))) as executor:
                futures = [executor.submit(self._process, item, queue, cancel_event) for item in pending]
                for future in as_completed(futures):
                    future.result()
        else:
            for item in pending:
                if queue.paused or (cancel_event is not None and cancel_event.is_set()):
                    break
                self._process(item, queue, cancel_event)
        return queue

    def estimate_prices(self, queue: FluxQueue) -> float:
        total = 0.0
        for item in queue.items:
            try:
                payload = self._payload(item)
                price = self.client.estimate_price(payload, model_id=FLUX_MODEL_ID)
                item.price = float(price.get("discounted_price", price.get("price")))
                item.error = ""
                total += item.price
            except Exception as exc:
                item.price = None
                item.error = f"No se pudo estimar: {exc}"
            self._notify(item)
        return round(total, 8)
