"""Standalone Venice multi-image edit controls and local output handling."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
from uuid import uuid4

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QTabWidget, QVBoxLayout, QWidget,
)

from app.sogni.auth import ApiKeyStore
from app.utils.paths import data_dir
from app.venice.client import VeniceApiError, VeniceClient, VeniceImageResult
from app.venice.history import VeniceHistoryStore
from app.venice.validation import (
    ModelProfile, REQUESTED_MODEL_IDS, build_multi_edit_payload,
    validate_prompt_and_images,
)
from app.ui.venice_batch import VeniceBatchWidget
from app.ui.numeric_inputs import WheelSafeComboBox


_MODEL_NAMES = {
    "qwen-image-2-1-pro-edit": "Qwen Image 2.1 Pro",
    "nano-banana-pro-edit": "Nano Banana Pro",
    "seedream-v5-pro-edit": "Seedream V5 Pro",
    "flux-2-max-edit": "FLUX 2 Max",
}
_IMAGE_SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


class _WorkerEvents(QObject):
    completed = Signal(object, object)


class _ReferenceRow:
    def __init__(self, owner: VeniceEditWidget) -> None:
        self.path: Path | None = None
        self.layout = QHBoxLayout()
        self.label = QLabel()
        self.path_label = QLabel("No image selected")
        self.select_button = QPushButton("Select / replace")
        self.remove_button = QPushButton("Remove")
        self.up_button = QPushButton("Up")
        self.down_button = QPushButton("Down")
        for control in (self.label, self.path_label, self.select_button,
                        self.remove_button, self.up_button, self.down_button):
            self.layout.addWidget(control)
        self.select_button.clicked.connect(lambda: owner._choose_reference(self))
        self.remove_button.clicked.connect(lambda: owner._set_row_path(self, None))
        self.up_button.clicked.connect(lambda: owner._move_row(self, -1))
        self.down_button.clicked.connect(lambda: owner._move_row(self, 1))


class VeniceEditWidget(QWidget):
    """A self-contained editor; MainWindow owns only its eventual tab registration."""

    def __init__(
        self, client: VeniceClient, key_store: ApiKeyStore,
        history_store: VeniceHistoryStore | None = None, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        key_path = Path(key_store.path)
        if key_store.env_name != "VENICE_API_KEY":
            key_path = key_path.with_name("venice_api_key.txt")
        self.key_store = ApiKeyStore(key_path, env_name="VENICE_API_KEY")
        self.history_store = history_store or VeniceHistoryStore()
        self.output_dir = data_dir() / "venice_outputs"
        self._profiles: dict[str, ModelProfile] = {}
        self._request_active = False
        self._pending_result: VeniceImageResult | None = None
        self._pending_snapshot: dict[str, object] | None = None
        self._catalog_active = False
        self._catalog_events = _WorkerEvents(self)
        self._request_events = _WorkerEvents(self)
        self._catalog_events.completed.connect(self._on_catalog_complete)
        self._request_events.completed.connect(self._on_request_complete)

        root = QVBoxLayout(self)
        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("Venice API key"))
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setText(self.key_store.get())
        self.client.api_key = self.key_store.get()
        key_row.addWidget(self.key_edit)
        self.save_key_button = QPushButton("Save key")
        self.save_key_button.clicked.connect(self._save_key)
        key_row.addWidget(self.save_key_button)
        self.key_source_label = QLabel()
        key_row.addWidget(self.key_source_label)
        root.addLayout(key_row)
        self._refresh_key_source()

        self.pages = QTabWidget()
        self.single_page = QWidget()
        self._single_layout = QVBoxLayout(self.single_page)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model"))
        self.model_combo = WheelSafeComboBox()
        self.model_combo.currentIndexChanged.connect(self._update_model_options)
        model_row.addWidget(self.model_combo)
        self.refresh_button = QPushButton("Refresh models")
        self.refresh_button.clicked.connect(self.refresh_catalog)
        model_row.addWidget(self.refresh_button)
        self._single_layout.addLayout(model_row)

        self._single_layout.addWidget(QLabel("Prompt"))
        self.prompt_edit = QTextEdit()
        self._single_layout.addWidget(self.prompt_edit)
        self._single_layout.addWidget(QLabel("References (top to bottom = request order)"))
        self._reference_rows = [_ReferenceRow(self) for _ in range(3)]
        for row in self._reference_rows:
            self._single_layout.addLayout(row.layout)
        self._update_reference_rows()

        options = QHBoxLayout()
        options.addWidget(QLabel("Aspect ratio"))
        self.aspect_combo = WheelSafeComboBox()
        options.addWidget(self.aspect_combo)
        options.addWidget(QLabel("Resolution"))
        self.resolution_combo = WheelSafeComboBox()
        options.addWidget(self.resolution_combo)
        options.addWidget(QLabel("Output format"))
        self.output_format_combo = WheelSafeComboBox()
        self.output_format_combo.addItems(["png", "jpeg", "webp"])
        options.addWidget(self.output_format_combo)
        self.safe_mode_check = QCheckBox("Safe mode")
        self.safe_mode_check.setChecked(True)
        options.addWidget(self.safe_mode_check)
        self._single_layout.addLayout(options)

        output_row = QHBoxLayout()
        self.output_dir_label = QLabel(str(self.output_dir))
        output_row.addWidget(self.output_dir_label)
        self.output_dir_button = QPushButton("Choose output folder")
        self.output_dir_button.clicked.connect(self._choose_output_dir)
        output_row.addWidget(self.output_dir_button)
        self._single_layout.addLayout(output_row)
        self.generate_button = QPushButton("Generate")
        self.generate_button.setEnabled(False)
        self.generate_button.clicked.connect(self.generate)
        self._single_layout.addWidget(self.generate_button)
        self.status_label = QLabel("Loading Venice model catalog…")
        self._single_layout.addWidget(self.status_label)
        self.preview_label = QLabel("Output preview")
        self._single_layout.addWidget(self.preview_label)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(["Time", "Model", "Prompt", "References", "Output"])
        self._single_layout.addWidget(self.history_table)
        self._load_history()
        self.batch_tab = VeniceBatchWidget(self.client, self.key_store, self)
        self.batch_tab.active_changed.connect(self._on_batch_active_changed)
        self.pages.addTab(self.single_page, "Single image")
        self.pages.addTab(self.batch_tab, "Batch")
        root.addWidget(self.pages)
        self.refresh_catalog()

    def _save_key(self) -> None:
        self._refresh_key_source()
        if getattr(self.batch_tab, "_active", False):
            self.status_label.setText("Venice API key is locked while the batch is running.")
            return
        if os.environ.get("VENICE_API_KEY", "").strip():
            self.status_label.setText("VENICE_API_KEY is active; file key was not saved.")
            return
        try:
            self.key_store.save(self.key_edit.text())
            self.client.api_key = self.key_store.get()
        except OSError:
            self.status_label.setText("Unable to save Venice API key.")
        else:
            self.status_label.setText("Venice API key saved.")

    def _refresh_key_source(self) -> None:
        environment_active = bool(os.environ.get("VENICE_API_KEY", "").strip())
        batch_active = getattr(getattr(self, "batch_tab", None), "_active", False)
        self.key_edit.setReadOnly(environment_active or batch_active)
        self.save_key_button.setEnabled(not environment_active and not batch_active)
        self.key_source_label.setText("VENICE_API_KEY is active; file key disabled."
                                      if environment_active else (
                                          "Venice key locked while batch runs." if batch_active else "Local Venice key"))

    def _on_batch_active_changed(self, active: bool) -> None:
        self._refresh_key_source()
        if active:
            self.key_source_label.setText("Venice key locked while batch runs.")

    def refresh_catalog(self) -> None:
        self._refresh_key_source()
        if self._catalog_active:
            return
        self._catalog_active = True
        self.refresh_button.setEnabled(False)
        self.status_label.setText("Loading Venice model catalog…")

        def work() -> None:
            try:
                profiles = self.client.list_edit_models()
                self._catalog_events.completed.emit(profiles, None)
            except Exception as exc:
                self._catalog_events.completed.emit(None, exc)

        threading.Thread(target=work, name="Venice catalog", daemon=True).start()

    def closeEvent(self, event) -> None:
        if hasattr(self, "batch_tab"):
            self.batch_tab.shutdown()
        super().closeEvent(event)

    def _on_catalog_complete(self, profiles: object, error: object) -> None:
        self._catalog_active = False
        self.refresh_button.setEnabled(True)
        if error is not None:
            self.status_label.setText(self._error_message(error))
            return
        self._profiles = {model_id: profiles[model_id] for model_id in REQUESTED_MODEL_IDS
                          if model_id in profiles and isinstance(profiles[model_id], ModelProfile)}
        previous = self.model_combo.currentData()
        self.model_combo.clear()
        for model_id in self._profiles:
            self.model_combo.addItem(_MODEL_NAMES[model_id], model_id)
        selected = previous if previous in self._profiles else (
            "qwen-image-2-1-pro-edit" if "qwen-image-2-1-pro-edit" in self._profiles else None)
        if selected:
            self.model_combo.setCurrentIndex(self.model_combo.findData(selected))
        self._update_model_options()
        self.generate_button.setEnabled(
            (bool(self._profiles) or self._pending_result is not None) and not self._request_active)
        self.status_label.setText("Venice models ready." if self._profiles else "No requested Venice edit models are available.")

    def _update_model_options(self, *_args) -> None:
        profile = self._profiles.get(self.model_combo.currentData())
        self.aspect_combo.clear()
        self.resolution_combo.clear()
        if profile is not None:
            self.aspect_combo.addItems(list(profile.aspect_ratios))
            self.resolution_combo.addItems(list(profile.resolutions))
            if profile.default_resolution:
                index = self.resolution_combo.findText(profile.default_resolution)
                if index >= 0:
                    self.resolution_combo.setCurrentIndex(index)
        self.aspect_combo.setEnabled(bool(profile and profile.aspect_ratios))
        self.resolution_combo.setEnabled(bool(profile and profile.resolutions))

    def set_reference_path(self, index: int, path: Path | None) -> None:
        self._set_row_path(self._reference_rows[index], Path(path) if path is not None else None)

    def _set_row_path(self, row: _ReferenceRow, path: Path | None) -> None:
        row.path = path
        self._update_reference_rows()

    def _choose_reference(self, row: _ReferenceRow) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Select Venice reference", "",
                                                   "Images (*.png *.jpg *.jpeg *.webp *.gif)")
        if selected:
            self._set_row_path(row, Path(selected))

    def move_reference(self, index: int, direction: int) -> None:
        self._move_row(self._reference_rows[index], direction)

    def _move_row(self, row: _ReferenceRow, direction: int) -> None:
        old = self._reference_rows.index(row)
        new = old + direction
        if not 0 <= new < len(self._reference_rows):
            return
        self._reference_rows[old], self._reference_rows[new] = self._reference_rows[new], self._reference_rows[old]
        layout = self._single_layout
        for reference in self._reference_rows:
            layout.removeItem(reference.layout)
        for index, reference in enumerate(self._reference_rows):
            layout.insertLayout(4 + index, reference.layout)
        self._update_reference_rows()

    def _update_reference_rows(self) -> None:
        for index, row in enumerate(self._reference_rows):
            row.label.setText(f"Reference {index + 1}")
            row.path_label.setText(row.path.name if row.path else "No image selected")
            row.path_label.setToolTip(str(row.path) if row.path else "")
            row.up_button.setEnabled(index > 0)
            row.down_button.setEnabled(index < len(self._reference_rows) - 1)
            row.remove_button.setEnabled(row.path is not None)

    def _ordered_reference_paths(self) -> list[Path]:
        return [Path(row.path) for row in self._reference_rows if row.path is not None]

    def _choose_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose Venice output folder", str(self.output_dir))
        if selected:
            self.output_dir = Path(selected)
            self.output_dir_label.setText(selected)

    def _build_request_snapshot(self) -> dict[str, object]:
        profile = self._profiles.get(self.model_combo.currentData())
        if profile is None:
            raise ValueError("Select an available Venice edit model.")
        return {
            "profile": profile, "prompt": self.prompt_edit.toPlainText(),
            "reference_paths": list(self._ordered_reference_paths()),
            "options": {
                "aspect_ratio": self.aspect_combo.currentText() if self.aspect_combo.isEnabled() else "",
                "resolution": self.resolution_combo.currentText() if self.resolution_combo.isEnabled() else "",
                "output_format": self.output_format_combo.currentText(),
                "safe_mode": self.safe_mode_check.isChecked(),
            },
            "output_dir": Path(self.output_dir),
        }

    def _build_payload_from_snapshot(self, snapshot: dict[str, object]) -> dict[str, object]:
        profile = snapshot["profile"]
        prompt = snapshot["prompt"]
        paths = list(snapshot["reference_paths"])
        options = dict(snapshot["options"])
        validate_prompt_and_images(profile=profile, prompt=prompt, image_paths=paths)
        images = [base64.b64encode(path.read_bytes()).decode("ascii") for path in paths]
        return build_multi_edit_payload(profile=profile, prompt=prompt, images=images, **options)

    def generate(self) -> None:
        if self._request_active:
            return
        if self._pending_result is not None:
            snapshot = dict(self._pending_snapshot)
            snapshot["reference_paths"] = list(snapshot["reference_paths"])
            snapshot["options"] = dict(snapshot["options"])
            snapshot["output_dir"] = Path(self.output_dir)
            result = self._pending_result
            self._request_active = True
            self.generate_button.setEnabled(False)
            self.status_label.setText("Saving received Venice image…")
            threading.Thread(target=lambda: self._save_result(result, snapshot),
                             name="Venice local save", daemon=True).start()
            return
        try:
            snapshot = self._build_request_snapshot()
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        self._request_active = True
        self.generate_button.setEnabled(False)
        self.status_label.setText("Generating Venice image…")

        def work() -> None:
            try:
                payload = self._build_payload_from_snapshot(snapshot)
                result = self.client.multi_edit(payload)
                self._save_result(result, snapshot)
            except Exception as exc:
                self._request_events.completed.emit(None, exc)

        threading.Thread(target=work, name="Venice image edit", daemon=True).start()

    def _save_result(self, result: VeniceImageResult, snapshot: dict[str, object]) -> None:
        try:
            saved = save_venice_output(result, snapshot["output_dir"])
        except Exception:
            self._request_events.completed.emit(("save_failed", result, snapshot), None)
        else:
            self._request_events.completed.emit(("saved", saved, result.request_id, snapshot), None)

    def _on_request_complete(self, completed: object, error: object) -> None:
        try:
            if error is not None:
                self.status_label.setText(self._error_message(error))
                return
            if completed[0] == "save_failed":
                _, self._pending_result, self._pending_snapshot = completed
                self.generate_button.setText("Save received image")
                self.status_label.setText(
                    "Venice image received, but local save failed. Choose an output folder and click "
                    "Save received image; no new request will be sent.")
                return
            _, output_path, request_id, snapshot = completed
            self._pending_result = None
            self._pending_snapshot = None
            self.generate_button.setText("Generate")
            preview = QPixmap(str(output_path))
            preview_unavailable = preview.isNull()
            if preview_unavailable:
                self.preview_label.clear()
                self.preview_label.setText("Preview unavailable")
            else:
                self.preview_label.setPixmap(preview)
            paths = snapshot["reference_paths"]
            record = {
                "provider": "Venice", "model_id": snapshot["profile"].id,
                "request_id": request_id,
                "prompt": snapshot["prompt"],
                "references": [{"slot": index + 1, "filename": path.name}
                               for index, path in enumerate(paths)],
                "parameters": dict(snapshot["options"]),
                "output_file": output_path.name,
            }
            self.history_store.add(record)
            self._insert_history_row(datetime.now(timezone.utc).isoformat(),
                                     snapshot["profile"].id, snapshot["prompt"],
                                     ", ".join(path.name for path in paths), str(output_path))
            if preview_unavailable:
                self.status_label.setText(f"Saved Venice image: {output_path}. Preview unavailable.")
            else:
                self.status_label.setText(f"Saved Venice image: {output_path}")
        except Exception:
            self.status_label.setText("Venice output was saved, but history or preview could not be updated.")
        finally:
            self._request_active = False
            self.generate_button.setEnabled(bool(self._profiles) or self._pending_result is not None)

    def _load_history(self) -> None:
        for record in reversed(self.history_store.list()):
            references = record.get("references", [])
            self._insert_history_row(record.get("created_at", ""), record.get("model_id", ""), "",
                                     ", ".join(item["filename"] for item in references), "")

    def _insert_history_row(self, when: str, model: str, prompt: str, references: str, output: str) -> None:
        self.history_table.insertRow(0)
        for column, value in enumerate((when, model, prompt, references, output)):
            self.history_table.setItem(0, column, QTableWidgetItem(str(value)))

    @staticmethod
    def _error_message(error: object) -> str:
        if isinstance(error, (VeniceApiError, ValueError)):
            return str(error)
        return "Venice request failed. Please try again."


def save_venice_output(result: VeniceImageResult, output_dir: Path) -> Path:
    """Save validated response bytes without putting request content in filenames."""
    suffix = _IMAGE_SUFFIXES.get(result.content_type)
    if suffix is None:
        raise ValueError("Venice returned an unsupported image content type.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"venice_{stamp}_{uuid4().hex[:8]}{suffix}"
    with path.open("xb") as stream:
        stream.write(result.content)
    return path
