"""Qt page for one-click Venice visual reconstruction batches."""

from __future__ import annotations

import json
from pathlib import Path
import threading

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.sogni.auth import ApiKeyStore
from app.venice.batch import (
    DEFAULT_VISION_MODEL,
    EDIT_MODEL,
    BatchConfig,
    BatchEvent,
    run_batch,
)
from app.venice.client import VeniceApiError, VeniceClient


class _BatchEvents(QObject):
    progress = Signal(object)
    completed = Signal(object, object)


class VeniceBatchWidget(QWidget):
    """Configure and run the two-stage folder reconstruction flow."""

    active_changed = Signal(bool)

    def __init__(self, client: VeniceClient, key_store: ApiKeyStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self.key_store = key_store
        self._closed = False
        self._active = False
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._close_target: QWidget | None = None
        self._close_when_done = False
        self._events = _BatchEvents(self)
        self._events.progress.connect(self._on_progress)
        self._events.completed.connect(self._on_complete)

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.source_dir_edit = QLineEdit()
        form.addRow("Source folder", self._with_button(self.source_dir_edit, self._choose_source))
        self.output_parent_edit = QLineEdit(str(Path.cwd() / "venice_batches"))
        form.addRow("Output parent", self._with_button(self.output_parent_edit, self._choose_output_parent))
        self.face_path_edit = QLineEdit()
        form.addRow("Face / hair reference (1)", self._with_button(self.face_path_edit, self._choose_face))
        self.body_path_edit = QLineEdit()
        form.addRow("Body reference (2)", self._with_button(self.body_path_edit, self._choose_body))
        self.vision_model_edit = QLineEdit(DEFAULT_VISION_MODEL)
        form.addRow("Prompt model (vision)", self.vision_model_edit)
        root.addLayout(form)

        master_row = QHBoxLayout()
        master_row.addWidget(QLabel("Editable master prompt"))
        self.load_txt_button = QPushButton("Load TXT")
        self.load_txt_button.clicked.connect(self._load_txt)
        master_row.addWidget(self.load_txt_button)
        root.addLayout(master_row)
        self.master_prompt_edit = QTextEdit()
        self.master_prompt_edit.setPlaceholderText("The vision model instructions for each source photo")
        root.addWidget(self.master_prompt_edit)
        self._load_default_master()

        controls = QHBoxLayout()
        self.process_button = QPushButton("Process folder")
        self.process_button.clicked.connect(self.process_folder)
        controls.addWidget(self.process_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        controls.addWidget(self.stop_button)
        self.status_label = QLabel("Ready for a Venice batch.")
        controls.addWidget(self.status_label)
        root.addLayout(controls)

        self.progress_table = QTableWidget(0, 5)
        self.progress_table.setHorizontalHeaderLabels(["Source", "Stage", "Status", "Prompt", "Output"])
        self.progress_table.itemSelectionChanged.connect(self._show_selected_prompt)
        root.addWidget(self.progress_table)
        root.addWidget(QLabel("Selected prompt preview"))
        self.prompt_preview = QTextEdit()
        self.prompt_preview.setReadOnly(True)
        root.addWidget(self.prompt_preview)
        self.output_folder_label = QLabel("Output folder: —")
        root.addWidget(self.output_folder_label)

    def _with_button(self, edit: QLineEdit, callback) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit)
        button = QPushButton("Choose")
        button.clicked.connect(callback)
        layout.addWidget(button)
        return container

    def _load_default_master(self) -> None:
        path = Path(__file__).resolve().parents[2] / "QWEN_2_1_Pro_Edit_Visual_Reconstruction_Master.txt"
        if path.is_file():
            try:
                self.master_prompt_edit.setPlainText(path.read_text(encoding="utf-8"))
            except OSError:
                pass

    def _load_txt(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Load master prompt", "", "Text files (*.txt);;All files (*)")
        if selected:
            try:
                self.master_prompt_edit.setPlainText(Path(selected).read_text(encoding="utf-8"))
            except OSError:
                self.status_label.setText("Unable to read the selected TXT file.")

    def _choose_source(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose source folder")
        if selected:
            self.source_dir_edit.setText(selected)

    def _choose_output_parent(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose output parent")
        if selected:
            self.output_parent_edit.setText(selected)

    def _choose_face(self) -> None:
        self._choose_image(self.face_path_edit)

    def _choose_body(self) -> None:
        self._choose_image(self.body_path_edit)

    def _choose_image(self, edit: QLineEdit) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "Choose reference image", "", "Images (*.png *.jpg *.jpeg *.webp *.gif)"
        )
        if selected:
            edit.setText(selected)

    def _build_config(self) -> BatchConfig:
        master = self.master_prompt_edit.toPlainText()
        if not master.strip():
            raise ValueError("Enter a master prompt before processing the folder.")
        values = {
            "source folder": self.source_dir_edit.text().strip(),
            "output parent": self.output_parent_edit.text().strip(),
            "face / hair reference": self.face_path_edit.text().strip(),
            "body reference": self.body_path_edit.text().strip(),
            "vision model": self.vision_model_edit.text().strip(),
        }
        for label, value in values.items():
            if not value:
                raise ValueError(f"Choose a {label} before processing the folder.")
        source_dir = Path(values["source folder"])
        if not source_dir.is_dir():
            raise ValueError("The source folder does not exist.")
        for label in ("face / hair reference", "body reference"):
            if not Path(values[label]).is_file():
                raise ValueError(f"The {label} does not exist.")
        return BatchConfig(
            input_dir=Path(source_dir),
            output_parent=Path(values["output parent"]),
            master_prompt=str(master),
            face_path=Path(values["face / hair reference"]),
            body_path=Path(values["body reference"]),
            vision_model=str(values["vision model"]),
        )

    def process_folder(self) -> None:
        if self._closed or self._active:
            return
        try:
            config = self._build_config()
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        self._active = True
        self._stop_event = threading.Event()
        self._active_config = config
        top_level = self.window()
        if top_level is not self:
            self._close_target = top_level
            top_level.installEventFilter(self)
        self.active_changed.emit(True)
        self.progress_table.setRowCount(0)
        self.prompt_preview.clear()
        self.output_folder_label.setText("Output folder: processing…")
        self.process_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status_label.setText("Loading Qwen Image 2.1 Pro model…")

        def work() -> None:
            try:
                profiles = self.client.list_edit_models()
                profile = profiles.get(EDIT_MODEL) if isinstance(profiles, dict) else None
                if profile is None:
                    raise ValueError("Qwen Image 2.1 Pro Edit is unavailable")
                output_dir = run_batch(
                    self.client, profile, config,
                    progress=self._events.progress.emit, stop_event=self._stop_event,
                )
            except Exception as exc:
                self._events.completed.emit(None, exc)
            else:
                self._events.completed.emit(output_dir, None)

        self._thread = threading.Thread(target=work, name="Venice visual batch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._active and self._stop_event is not None:
            self._stop_event.set()
            self.stop_button.setEnabled(False)
            self.status_label.setText("Stop requested; finishing the current photo…")

    def eventFilter(self, watched, event) -> bool:
        if watched is self._close_target and event.type() == QEvent.Type.Close and self._active:
            self._close_when_done = True
            self.stop()
            event.ignore()
            return True
        return super().eventFilter(watched, event)

    def _on_progress(self, event: BatchEvent) -> None:
        if self._closed:
            return
        row = event.index - 1
        while self.progress_table.rowCount() <= row:
            self.progress_table.insertRow(self.progress_table.rowCount())
        self.progress_table.setItem(row, 0, QTableWidgetItem(event.filename))
        self.progress_table.setItem(row, 1, QTableWidgetItem(event.stage))
        self.progress_table.setItem(row, 2, QTableWidgetItem(event.status))
        if event.prompt_path is not None:
            prompt_item = QTableWidgetItem(event.prompt_path.name)
            prompt_item.setData(Qt.UserRole, str(event.prompt_path))
            self.progress_table.setItem(row, 3, prompt_item)
        if event.output_path is not None:
            self.progress_table.setItem(row, 4, QTableWidgetItem(str(event.output_path)))
        if event.error:
            self.progress_table.setItem(row, 2, QTableWidgetItem(f"{event.status}: {event.error}"))
        self.status_label.setText(f"{event.filename}: {event.status}")

    def _show_selected_prompt(self) -> None:
        row = self.progress_table.currentRow()
        if row < 0:
            return
        item = self.progress_table.item(row, 3)
        if item is None:
            self.prompt_preview.clear()
            return
        raw_path = item.data(Qt.UserRole)
        if not raw_path:
            self.prompt_preview.clear()
            return
        try:
            self.prompt_preview.setPlainText(Path(str(raw_path)).read_text(encoding="utf-8"))
        except OSError:
            self.prompt_preview.setPlainText("Saved prompt is not available locally.")

    def _on_complete(self, output_dir: object, error: object) -> None:
        if self._closed:
            return
        close_target = self._detach_close_filter()
        close_when_done = self._close_when_done
        self._close_when_done = False
        self._active = False
        self._thread = None
        self._stop_event = None
        self.active_changed.emit(False)
        self.process_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if error is not None:
            self.status_label.setText(self._error_message(error))
            self.output_folder_label.setText("Output folder: —")
        else:
            self.output_folder_label.setText(f"Output folder: {output_dir}")
            self.status_label.setText(self._run_summary(Path(output_dir)))
        if close_when_done and close_target is not None:
            QTimer.singleShot(0, close_target.close)

    def _detach_close_filter(self) -> QWidget | None:
        target = self._close_target
        self._close_target = None
        if target is not None:
            target.removeEventFilter(self)
        return target

    @staticmethod
    def _run_summary(output_dir: Path) -> str:
        try:
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            items = manifest["items"]
            if not isinstance(items, list):
                raise ValueError("Invalid batch manifest")
            completed = sum(item.get("status") == "completed" for item in items)
            failed = sum(item.get("status") == "failed" for item in items)
            stopped = sum(item.get("status") == "stopped" for item in items)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return "Venice batch ended; manifest unavailable."
        state = "completed" if completed == len(items) else "ended"
        return f"Venice batch {state}: {completed} completed, {failed} failed, {stopped} stopped."

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop()
        self._active = False
        self.stop_button.setEnabled(False)
        self._detach_close_filter()

    @staticmethod
    def _error_message(error: object) -> str:
        if isinstance(error, (VeniceApiError, ValueError)):
            return str(error)
        return "Venice batch failed. Please check the inputs and try again."
