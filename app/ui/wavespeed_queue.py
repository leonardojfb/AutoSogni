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


class WaveSpeedQueueWidget(QWidget):
    queue_changed = Signal()
    start_requested = Signal()
    pause_requested = Signal()
    retry_requested = Signal(str)
    retry_failed_requested = Signal()
    clear_completed_requested = Signal()

    COLUMNS = (
        "#",
        "Video",
        "Prompt",
        "Resolution",
        "Aspect",
        "Duration",
        "Audio",
        "Expand",
        "Seed",
        "Price",
        "Status",
        "Task",
        "Output",
        "Error",
        "Retry",
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._queue = WaveSpeedQueue()
        self._retry_buttons: dict[str, QPushButton] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Cola secuencial WaveSpeed · un frame compartido, un video por fila"))

        frame_row = QHBoxLayout()
        self.frame_edit = QLineEdit()
        self.frame_edit.setPlaceholderText("Frame compartido para toda la cola")
        choose_frame = QPushButton("Seleccionar frame")
        choose_frame.clicked.connect(self._choose_frame)
        remove_frame = QPushButton("Quitar frame")
        remove_frame.clicked.connect(lambda: self.frame_edit.clear())
        frame_row.addWidget(QLabel("Frame"))
        frame_row.addWidget(self.frame_edit, 1)
        frame_row.addWidget(choose_frame)
        frame_row.addWidget(remove_frame)
        layout.addLayout(frame_row)

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Carpeta de salida; usa la carpeta por defecto si queda vacía")
        choose_output = QPushButton("Carpeta")
        choose_output.clicked.connect(self._choose_output)
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Output"))
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(choose_output)
        layout.addLayout(output_row)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked | QAbstractItemView.EditKeyPressed
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(self.table)

        controls = QHBoxLayout()
        add_video = QPushButton("Agregar video")
        add_video.clicked.connect(self._choose_videos)
        remove_selected = QPushButton("Quitar seleccionado")
        remove_selected.clicked.connect(self._remove_selected)
        start = QPushButton("Iniciar cola")
        start.clicked.connect(self.start_requested.emit)
        pause = QPushButton("Pausar después del actual")
        pause.clicked.connect(self.pause_requested.emit)
        retry_failed = QPushButton("Reintentar fallidos")
        retry_failed.clicked.connect(self.retry_failed_requested.emit)
        clear_completed = QPushButton("Limpiar completados")
        clear_completed.clicked.connect(self.clear_completed_requested.emit)
        self.start_button = start
        self.pause_button = pause
        controls.addWidget(add_video)
        controls.addWidget(remove_selected)
        controls.addWidget(start)
        controls.addWidget(pause)
        controls.addWidget(retry_failed)
        controls.addWidget(clear_completed)
        layout.addLayout(controls)

        self.status_label = QLabel("Cola vacía.")
        layout.addWidget(self.status_label)

    def _choose_frame(self) -> None:
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
        self._queue.items.append(item)
        self._append_row(item)
        self._update_status()
        self.queue_changed.emit()
        return item

    def add_video(self, video_path: str) -> WaveSpeedQueueItem:
        return self.add_item_for_test(video_path)

    def set_frame_path(self, frame_path: str) -> None:
        self._queue.frame_path = frame_path
        self.frame_edit.setText(frame_path)
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
        cells = {
            0: self._text_item(row + 1),
            1: self._text_item(Path(item.video_path).name),
            2: self._text_item(item.prompt, editable=True),
            3: self._text_item(item.resolution, editable=True),
            4: self._text_item(item.aspect_ratio, editable=True),
            5: self._text_item(item.duration, editable=True),
            6: self._check_item(item.enable_audio),
            7: self._check_item(item.enable_prompt_expansion),
            8: self._text_item(item.seed, editable=True),
            9: self._text_item("-"),
            10: self._text_item(item.status),
            11: self._text_item(""),
            12: self._text_item(""),
            13: self._text_item(""),
        }
        cells[1].setData(Qt.UserRole, item.video_path)
        cells[10].setData(Qt.UserRole, item.item_id)
        self.table.blockSignals(True)
        for column, cell in cells.items():
            self.table.setItem(row, column, cell)
        self.table.blockSignals(False)
        retry = QPushButton("Reintentar")
        retry.setEnabled(item.status == QueueStatus.FAILED)
        retry.clicked.connect(lambda _checked=False, item_id=item.item_id: self.retry_requested.emit(item_id))
        self.table.setCellWidget(row, 14, retry)
        self._retry_buttons[item.item_id] = retry

    def _item_changed(self, cell: QTableWidgetItem) -> None:
        if cell.column() in {2, 3, 4, 5, 6, 7, 8}:
            self._sync_item(cell.row())
            self.queue_changed.emit()

    def _sync_item(self, row: int) -> WaveSpeedQueueItem | None:
        if not 0 <= row < self.table.rowCount():
            return None
        item_id = self.table.item(row, 10).data(Qt.UserRole)
        item = next((value for value in self._queue.items if value.item_id == item_id), None)
        if item is None:
            return None
        item.prompt = self.table.item(row, 2).text()
        item.resolution = self.table.item(row, 3).text()
        item.aspect_ratio = self.table.item(row, 4).text()
        try:
            item.duration = int(self.table.item(row, 5).text())
        except ValueError:
            pass
        item.enable_audio = self.table.item(row, 6).checkState() == Qt.Checked
        item.enable_prompt_expansion = self.table.item(row, 7).checkState() == Qt.Checked
        try:
            item.seed = int(self.table.item(row, 8).text())
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
            self.table.item(row, 0).setText(str(row + 1))

    def _update_status(self) -> None:
        self.status_label.setText(f"{len(self._queue.items)} ítem(s) en cola.")

    def load_queue(self, queue: WaveSpeedQueue) -> None:
        self._queue = queue
        self.frame_edit.setText(queue.frame_path)
        self.output_edit.setText(queue.output_dir)
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
        return self._queue

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.pause_button.setEnabled(running)

    def set_item_update(self, updated: WaveSpeedQueueItem) -> None:
        existing = next((item for item in self._queue.items if item.item_id == updated.item_id), None)
        if existing is not None:
            existing.__dict__.update(updated.__dict__)
        for row in range(self.table.rowCount()):
            if self.table.item(row, 10).data(Qt.UserRole) != updated.item_id:
                continue
            self.table.blockSignals(True)
            self.table.item(row, 9).setText("-" if updated.price is None else f"${updated.price:.4f}")
            self.table.item(row, 10).setText(updated.status)
            self.table.item(row, 11).setText(updated.task_id)
            self.table.item(row, 12).setText(updated.output_file)
            self.table.item(row, 13).setText(updated.error)
            self.table.blockSignals(False)
            self._retry_buttons[updated.item_id].setEnabled(updated.status == QueueStatus.FAILED)
            break

    def has_retry_control(self, item_id: str) -> bool:
        return item_id in self._retry_buttons

    def set_error(self, message: str) -> None:
        self.status_label.setText(f"Error: {message}")
