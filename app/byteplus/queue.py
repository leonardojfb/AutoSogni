from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.utils.paths import data_dir


class QueueStatus:
    PENDING = "pending"; RUNNING = "running"; COMPLETED = "completed"; FAILED = "failed"; PAUSED = "paused"

def _now(): return datetime.now(timezone.utc).isoformat()

@dataclass
class BytePlusQueueItem:
    item_id: str = field(default_factory=lambda: uuid4().hex)
    snapshot: dict = field(default_factory=dict)
    status: str = QueueStatus.PENDING
    task_id: str = ""
    price: float | None = None
    output_file: str = ""
    last_frame_file: str = ""
    error: str = ""
    created_at: str = field(default_factory=_now)
    def retry(self): self.status, self.task_id, self.price, self.output_file, self.last_frame_file, self.error = QueueStatus.PENDING, "", None, "", "", ""

@dataclass
class BytePlusQueue:
    items: list[BytePlusQueueItem] = field(default_factory=list)
    output_dir: str = ""
    paused: bool = False

@dataclass
class BytePlusCampaign:
    campaign_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = "Nueva campaña BytePlus"
    queue: BytePlusQueue = field(default_factory=BytePlusQueue)

class BytePlusCampaignStore:
    def __init__(self, path: Path | None = None): self.path = path or data_dir() / "byteplus_campaigns.json"
    def save(self, campaign: BytePlusCampaign, active: bool = False):
        campaigns, active_id = self.load()
        campaigns = [value for value in campaigns if value.campaign_id != campaign.campaign_id] + [campaign]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"active_id": campaign.campaign_id if active else active_id, "campaigns": [asdict(value) for value in campaigns]}, ensure_ascii=False, indent=2), encoding="utf-8")
    def load(self):
        if not self.path.exists(): return [], None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        campaigns = []
        for raw in data.get("campaigns", []):
            items = [BytePlusQueueItem(**{key: value for key, value in item.items() if key in BytePlusQueueItem.__dataclass_fields__}) for item in raw.get("queue", {}).get("items", [])]
            for item in items:
                if item.status == QueueStatus.RUNNING: item.status = QueueStatus.PENDING
            campaigns.append(BytePlusCampaign(raw.get("campaign_id", uuid4().hex), raw.get("name", "Nueva campaña BytePlus"), BytePlusQueue(items, raw.get("queue", {}).get("output_dir", ""), raw.get("queue", {}).get("paused", False))))
        return campaigns, data.get("active_id")
