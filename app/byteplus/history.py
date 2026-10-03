from __future__ import annotations
import json
from pathlib import Path
from app.utils.paths import data_dir
class BytePlusHistoryStore:
    def __init__(self, path: Path | None = None): self.path = path or data_dir() / "byteplus_history.json"
    def add(self, row: dict):
        rows = self.list(); rows.insert(0, row); self.path.parent.mkdir(parents=True, exist_ok=True); self.path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    def list(self): return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
