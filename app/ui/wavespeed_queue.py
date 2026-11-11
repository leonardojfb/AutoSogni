from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.wavespeed.queue import QueueStatus, WaveSpeedQueue, WaveSpeedQueueItem
from app.ui.numeric_inputs import WheelSafeComboBox


class WaveSpeedQueueWidget(QWidget):
    queue_changed = Signal()
    start_requested = Signal()
    pause_requested = Signal()
    retry_requested = Signal(str)
    retry_failed_requested = Signal()
    clear_completed_requested = Signal()
    estimate_requested = Signal()
    campaign_selected = Signal(str)
    new_campaign_requested = Signal()
    save_campaign_requested = Signal()
    delete_campaign_requested = Signal()

    INDEX_COLUMN = 0
    REFERENCES_COLUMN = 1
    PROMPT_COLUMN = 2
    RESOLUTION_COLUMN = 3
    ASPECT_COLUMN = 4
    DURATION_COLUMN = 5
    AUDIO_COLUMN = 6
    EXPANSION_COLUMN = 7
    SEED_COLUMN = 8
    PRICE_COLUMN = 9
    STATUS_COLUMN = 10
    TASK_COLUMN = 11
    OUTPUT_COLUMN = 12
    ERROR_COLUMN = 13
    RETRY_COLUMN = 14
    MODEL_COLUMN = 15

    COLUMNS = (
        "#", "Referencias", "Prompt", "Resolution", "Aspect", "Duration",
        "Audio", "Expand / Web", "Seed", "Price", "Status", "Task", "Output",
        "Error", "Retry", "Model",
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._queue = WaveSpeedQueue()
        self._running = False
        self._retry_buttons: dict[str, QPushButton] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        campaign_row = QHBoxLayout()
        campaign_row.addWidget(QLabel("Campaña"))
        self.campaign_combo = WheelSafeComboBox()
        self.campaign_combo.currentIndexChanged.connect(self._campaign_index_changed)
        self.campaign_name_edit = QLineEdit("Nueva campaña")
        self.campaign_name_edit.setPlaceholderText("Nombre de la campaña")
        self.campaign_new_button = QPushButton("Nueva campaña")
        self.campaign_new_button.clicked.connect(self.new_campaign_requested.emit)
        self.campaign_save_button = QPushButton("Guardar campaña")
        self.campaign_save_button.clicked.connect(self.save_campaign_requested.emit)
        self.campaign_delete_button = QPushButton("Eliminar campaña")
        self.campaign_delete_button.clicked.connect(self.delete_campaign_requested.emit)
        self.execution_mode_combo = WheelSafeComboBox()
        self.execution_mode_combo.addItem("Secuencial", "sequential")
        self.execution_mode_combo.addItem("Paralelo", "parallel")
        self.execution_mode_combo.currentIndexChanged.connect(lambda _index: self.queue_changed.emit())
        campaign_row.addWidget(self.campaign_combo, 1)
        campaign_row.addWidget(self.campaign_name_edit, 1)
        campaign_row.addWidget(self.execution_mode_combo)
        campaign_row.addWidget(self.campaign_new_button)
        campaign_row.addWidget(self.campaign_save_button)
        campaign_row.addWidget(self.campaign_delete_button)
        layout.addLayout(campaign_row)

        frame_row = QHBoxLayout()
        self.frame_edit = QLineEdit()
        self.frame_edit.setPlaceholderText("Frame compartido de campañas antiguas")
        choose_frame = QPushButton("Seleccionar frame")
        choose_frame.clicked.connect(self._choose_frame)
        remove_frame = QPushButton("Quitar frame")
        remove_frame.clicked.connect(lambda: self.frame_edit.clear())
        self.choose_frame_button = choose_frame
        self.remove_frame_button = remove_frame
        frame_row.addWidget(QLabel("Frame anterior"))
        frame_row.addWidget(self.frame_edit, 1)
        frame_row.addWidget(choose_frame)
        frame_row.addWidget(remove_frame)
        self.legacy_frame_panel = QWidget()
        self.legacy_frame_panel.setLayout(frame_row)
        layout.addWidget(self.legacy_frame_panel)

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Carpeta de salida; usa la carpeta por defecto si queda vacía")

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked | QAbstractItemView.EditKeyPressed
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.itemChanged.connect(self._item_changed)
        self.table.setMinimumHeight(150)
        layout.addWidget(self.table)

        controls = QHBoxLayout()
        add_video = QPushButton("Agregar video")
        add_video.clicked.connect(self._choose_videos)
        add_video.setVisible(False)
        remove_selected = QPushButton("Quitar seleccionado")
        remove_selected.clicked.connect(self._remove_selected)
        self.add_video_button = add_video
        self.remove_selected_button = remove_selected
        start = QPushButton("Iniciar cola")
        start.clicked.connect(self.start_requested.emit)
        pause = QPushButton("Pausar después del actual")
        pause.clicked.connect(self.pause_requested.emit)
        retry_failed = QPushButton("Reintentar fallidos")
        retry_failed.clicked.connect(self.retry_failed_requested.emit)
        clear_completed = QPushButton("Limpiar completados")
        clear_completed.clicked.connect(self.clear_completed_requested.emit)
        estimate = QPushButton("Estimar costos")
        estimate.clicked.connect(self.estimate_requested.emit)
        self.estimate_button = estimate
        self.retry_failed_button = retry_failed
        self.clear_completed_button = clear_completed
        self.total_price_label = QLabel("Total estimado: no calculado")
        self.start_button = start
        self.pause_button = pause
        controls.addWidget(add_video)
        controls.addWidget(remove_selected)
        controls.addWidget(start)
        controls.addWidget(pause)
        controls.addWidget(retry_failed)
        controls.addWidget(clear_completed)
        controls.addWidget(estimate)
        controls.addWidget(self.total_price_label)
        layout.addLayout(controls)

        self.status_label = QLabel("Cola vacía.")
        layout.addWidget(self.status_label)
        self._update_status()

    def _choose_frame(self) -> None:
        if self._queue.items:
            return
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar frame compartido",
            filter="Images (*.png *.jpg *.jpeg *.webp *.gif)",
        )
        if selected:
            self.frame_edit.setText(str(Path(selected).resolve()))
            self.queue_changed.emit()

    def _choose_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de salida")
        if selected:
            self.output_edit.setText(selected)
            self.queue_changed.emit()

    def _choose_videos(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(self, "Seleccionar videos", filter="Videos (*.mp4 *.mov)")
        for path in selected:
            self.add_item_for_test(str(Path(path).resolve()))

    def add_item_for_test(self, video_path: str) -> WaveSpeedQueueItem:
        item = WaveSpeedQueueItem(video_path=video_path)
        self.append_item(item)
        return item

    def append_item(self, item: WaveSpeedQueueItem) -> None:
        self._queue.items.append(item)
        self._append_row(item)
        self._update_status()
        self.queue_changed.emit()

    def add_video(self, video_path: str) -> WaveSpeedQueueItem:
        return self.add_item_for_test(video_path)

    def set_frame_path(self, frame_path: str) -> None:
        self._queue.frame_path = frame_path
        self.frame_edit.setText(frame_path)
        self._update_status()
        self.queue_changed.emit()

    def _text_item(self, value: object, *, editable: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(str(value))
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        return item

    def _check_item(self, checked: bool) -> QTableWidgetItem:
        item = QTableWidgetItem()
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        return item

    def _append_row(self, item: WaveSpeedQueueItem) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        references = [*(f"I: {Path(name).name}" for name in item.reference_images),
                      *(f"V: {Path(name).name}" for name in item.reference_videos),
                      *(f"A: {Path(name).name}" for name in item.reference_audios)]
        if not references and item.video_path:
            references = [f"V: {Path(item.video_path).name}"]
        cells = {
            self.INDEX_COLUMN: self._text_item(row + 1),
            self.REFERENCES_COLUMN: self._text_item(", ".join(references)),
            self.PROMPT_COLUMN: self._text_item(item.prompt, editable=True),
            self.RESOLUTION_COLUMN: self._text_item(item.resolution, editable=True),
            self.ASPECT_COLUMN: self._text_item(item.aspect_ratio, editable=True),
            self.DURATION_COLUMN: self._text_item(item.duration, editable=True),
            self.AUDIO_COLUMN: self._check_item(item.enable_audio),
            self.EXPANSION_COLUMN: self._check_item(item.enable_prompt_expansion),
            self.SEED_COLUMN: self._text_item(item.seed, editable=True),
            self.PRICE_COLUMN: self._text_item("-" if item.price is None else f"${item.price:.4f}"),
            self.STATUS_COLUMN: self._text_item(item.status),
            self.TASK_COLUMN: self._text_item(item.task_id),
            self.OUTPUT_COLUMN: self._text_item(item.output_file),
            self.ERROR_COLUMN: self._text_item(item.error),
            self.MODEL_COLUMN: self._text_item(self._model_name(item)),
        }
        cells[self.REFERENCES_COLUMN].setData(Qt.UserRole, item.video_path)
        cells[self.REFERENCES_COLUMN].setToolTip("\n".join([*item.reference_images,
                                                               *item.reference_videos, *item.reference_audios]))
        cells[self.STATUS_COLUMN].setData(Qt.UserRole, item.item_id)
        self.table.blockSignals(True)
        for column, cell in cells.items():
            self.table.setItem(row, column, cell)
        self.table.blockSignals(False)
        retry = QPushButton("Reintentar")
        retry.setEnabled(item.status == QueueStatus.FAILED)
        retry.clicked.connect(lambda _checked=False, item_id=item.item_id: self.retry_requested.emit(item_id))
        self.table.setCellWidget(row, self.RETRY_COLUMN, retry)
        self._retry_buttons[item.item_id] = retry

    def _item_changed(self, cell: QTableWidgetItem) -> None:
        if cell.column() in {self.PROMPT_COLUMN, self.RESOLUTION_COLUMN, self.ASPECT_COLUMN,
                             self.DURATION_COLUMN, self.AUDIO_COLUMN, self.EXPANSION_COLUMN,
                             self.SEED_COLUMN}:
            self._sync_item(cell.row())
            self.queue_changed.emit()

    def _sync_item(self, row: int) -> WaveSpeedQueueItem | None:
        if not 0 <= row < self.table.rowCount():
            return None
        item_id = self.table.item(row, self.STATUS_COLUMN).data(Qt.UserRole)
        item = next((value for value in self._queue.items if value.item_id == item_id), None)
        if item is None:
            return None
        item.prompt = self.table.item(row, self.PROMPT_COLUMN).text()
        item.resolution = self.table.item(row, self.RESOLUTION_COLUMN).text()
        item.aspect_ratio = self.table.item(row, self.ASPECT_COLUMN).text()
        try:
            item.duration = int(self.table.item(row, self.DURATION_COLUMN).text())
        except ValueError:
            pass
        item.enable_audio = self.table.item(row, self.AUDIO_COLUMN).checkState() == Qt.Checked
        item.enable_prompt_expansion = self.table.item(row, self.EXPANSION_COLUMN).checkState() == Qt.Checked
        try:
            item.seed = int(self.table.item(row, self.SEED_COLUMN).text())
        except ValueError:
            pass
        return item

    def _remove_selected(self) -> None:
        selected = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in selected:
            item = self._sync_item(row)
            if item is not None:
                self._queue.items = [value for value in self._queue.items if value.item_id != item.item_id]
                self._retry_buttons.pop(item.item_id, None)
            self.table.removeRow(row)
        self._renumber_rows()
        self._update_status()
        self.queue_changed.emit()

    def _renumber_rows(self) -> None:
        for row in range(self.table.rowCount()):
            self.table.item(row, self.INDEX_COLUMN).setText(str(row + 1))

    def _update_status(self) -> None:
        legacy = bool(self._queue.frame_path) and (
            not self._queue.items or any(item.video_path and not (
                item.reference_images or item.reference_videos or item.reference_audios
            ) for item in self._queue.items)
        )
        self.legacy_frame_panel.setVisible(legacy)
        frame_locked = bool(self._queue.items)
        self.frame_edit.setReadOnly(frame_locked)
        self.choose_frame_button.setEnabled(not frame_locked)
        self.remove_frame_button.setEnabled(not frame_locked)
        self.status_label.setText(f"{len(self._queue.items)} ítem(s) en cola.")

    def load_queue(self, queue: WaveSpeedQueue) -> None:
        self._queue = queue
        self.frame_edit.setText(queue.frame_path)
        self.output_edit.setText(queue.output_dir)
        self.execution_mode_combo.blockSignals(True)
        mode_index = self.execution_mode_combo.findData(queue.execution_mode)
        self.execution_mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self.execution_mode_combo.blockSignals(False)
        self.table.setRowCount(0)
        self._retry_buttons.clear()
        for item in queue.items:
            self._append_row(item)
        self._update_status()

    def queue(self) -> WaveSpeedQueue:
        for row in range(self.table.rowCount()):
            self._sync_item(row)
        self._queue.frame_path = self.frame_edit.text().strip()
        self._queue.output_dir = self.output_edit.text().strip()
        self._queue.execution_mode = str(self.execution_mode_combo.currentData() or "sequential")
        return self._queue

    def set_running(self, running: bool) -> None:
        self._running = running
        self.start_button.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.table.setEditTriggers(
            QAbstractItemView.NoEditTriggers if running else
            QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked | QAbstractItemView.EditKeyPressed
        )
        for control in (self.add_video_button, self.remove_selected_button,
                        self.retry_failed_button, self.clear_completed_button,
                        self.campaign_combo, self.campaign_name_edit,
                        self.campaign_new_button, self.campaign_save_button,
                        self.campaign_delete_button, self.execution_mode_combo,
                        self.output_edit, self.estimate_button):
            control.setEnabled(not running)
        for item in self._queue.items:
            button = self._retry_buttons.get(item.item_id)
            if button is not None:
                button.setEnabled(not running and item.status == QueueStatus.FAILED)

    def set_item_update(self, updated: WaveSpeedQueueItem) -> None:
        existing = next((item for item in self._queue.items if item.item_id == updated.item_id), None)
        if existing is not None:
            existing.__dict__.update(updated.__dict__)
        for row in range(self.table.rowCount()):
            if self.table.item(row, self.STATUS_COLUMN).data(Qt.UserRole) != updated.item_id:
                continue
            self.table.blockSignals(True)
            self.table.item(row, self.PRICE_COLUMN).setText("-" if updated.price is None else f"${updated.price:.4f}")
            self.table.item(row, self.STATUS_COLUMN).setText(updated.status)
            self.table.item(row, self.TASK_COLUMN).setText(updated.task_id)
            self.table.item(row, self.OUTPUT_COLUMN).setText(updated.output_file)
            self.table.item(row, self.ERROR_COLUMN).setText(updated.error)
            self.table.blockSignals(False)
            self._retry_buttons[updated.item_id].setEnabled(
                not self._running and updated.status == QueueStatus.FAILED
            )
            break

    @staticmethod
    def _model_name(item: WaveSpeedQueueItem) -> str:
        return "Seedance 2.0" if "seedance" in item.model_id else "WAN 3.0"

    def has_retry_control(self, item_id: str) -> bool:
        return item_id in self._retry_buttons

    def set_error(self, message: str) -> None:
        self.status_label.setText(f"Error: {message}")

    def _campaign_index_changed(self, index: int) -> None:
        if index >= 0:
            self.campaign_selected.emit(str(self.campaign_combo.itemData(index) or ""))

    def set_campaigns(self, campaigns, active_id: str | None = None) -> None:
        self.campaign_combo.blockSignals(True)
        self.campaign_combo.clear()
        selected_index = -1
        for index, campaign in enumerate(campaigns):
            self.campaign_combo.addItem(campaign.name, campaign.campaign_id)
            if campaign.campaign_id == active_id:
                selected_index = index
        if selected_index >= 0:
            self.campaign_combo.setCurrentIndex(selected_index)
        self.campaign_combo.blockSignals(False)

    def selected_campaign_id(self) -> str:
        return str(self.campaign_combo.currentData() or "")

    def set_campaign_name(self, name: str) -> None:
        self.campaign_name_edit.setText(name)

    def campaign_name(self) -> str:
        return self.campaign_name_edit.text().strip() or "Nueva campaña"

    def set_estimating(self, estimating: bool) -> None:
        self.estimate_button.setEnabled(not estimating)
        if estimating:
            self.total_price_label.setText("Total estimado: calculando...")

    def set_total_price(self, total: float | None, error: str = "") -> None:
        if error:
            self.total_price_label.setText(f"Total estimado: error: {error}")
        elif total is None:
            self.total_price_label.setText("Total estimado: no calculado")
        else:
            self.total_price_label.setText(f"Total estimado: ${total:.4f} USD")
