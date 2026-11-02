"""Sanitized, bounded local history for Venice image edits."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.utils.paths import data_dir


_PARAMETER_KEYS = ("aspect_ratio", "resolution", "output_format", "safe_mode", "seed")
_RECORD_KEYS = ("provider", "model_id", "request_id", "created_at", "references", "parameters")
_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for_path(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve()))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.Lock())


def sanitize_history_record(record: dict[str, object]) -> dict[str, object]:
    """Copy only explicitly approved, non-prompt edit metadata into history."""
    safe: dict[str, object] = {}
    for key in ("provider", "model_id", "request_id", "created_at"):
        value = record.get(key)
        if isinstance(value, str) and value and not _looks_sensitive(value):
            safe[key] = value[:200]

    references = record.get("references")
    if isinstance(references, list):
        safe_references = []
        for reference in references:
            if not isinstance(reference, dict):
                continue
            slot, filename = reference.get("slot"), reference.get("filename")
            if (isinstance(slot, int) and not isinstance(slot, bool) and slot > 0
                    and isinstance(filename, str) and filename and Path(filename).name == filename
                    and not _looks_sensitive(filename)):
                safe_references.append({"slot": slot, "filename": filename[:255]})
        if safe_references:
            safe["references"] = safe_references

    parameters = record.get("parameters")
    if isinstance(parameters, dict):
        safe_parameters = {}
        for key in _PARAMETER_KEYS:
            value = parameters.get(key)
            if isinstance(value, (str, int, float, bool)) and not _looks_sensitive(value):
                safe_parameters[key] = value
        if safe_parameters:
            safe["parameters"] = safe_parameters
    return {key: safe[key] for key in _RECORD_KEYS if key in safe}


def _looks_sensitive(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return ("/" in value or "\\" in value or "://" in value
            or "bearer " in lowered or "authorization" in lowered
            or "api_key" in lowered or "secret" in lowered or "base64" in lowered)


class VeniceHistoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or data_dir() / "venice_history.json")
        self._lock = _lock_for_path(self.path)

    def list(self) -> list[dict[str, object]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        return [sanitize_history_record(item) for item in value if isinstance(item, dict)][:100]

    def add(self, record: dict[str, object]) -> None:
        safe = sanitize_history_record(record)
        safe.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        with self._lock:
            rows = [safe, *self.list()]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.path.parent,
                    prefix=f"{self.path.name}.", suffix=".tmp", delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(json.dumps(rows[:100], ensure_ascii=False, indent=2))
                os.replace(temporary, self.path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
