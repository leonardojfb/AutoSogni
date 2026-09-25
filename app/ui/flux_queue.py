from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.wavespeed.flux_queue import FluxQueue, FluxQueueItem
from app.wavespeed.queue import QueueStatus


class FluxQueueWidget(QWidget):
    queue_changed = Signal()
    campaign_selected = Signal(str)
    new_campaign_requested = Signal()
    save_campaign_requested = Signal()
    delete_campaign_requested = Signal()
    start_requested = Signal()
    pause_requested = Signal()
    retry_requested = Signal(str)
    retry_failed_requested = Signal()
    clear_completed_requested = Signal()
    estimate_requested = Signal()

    COLUMNS = ("#", "Imágenes", "Prompt", "Size", "Seed", "Precio", "Estado", "Task",
               "Archivo", "Error", "Reintentar")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._queue = FluxQueue()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        campaigns = QHBoxLayout()
        self.campaign_combo = QComboBox()
        self.campaign_combo.currentIndexChanged.connect(
            lambda index: self.campaign_selected.emit(str(self.campaign_combo.itemData(index) or "")) if index >= 0 else None)
        self.campaign_name_edit = QLineEdit("Nueva campaña Flux")
        self.execution_mode_combo = QComboBox()
        self.execution_mode_combo.addItem("Secuencial", "sequential")
        self.execution_mode_combo.addItem("Paralelo", "parallel")
        self.execution_mode_combo.currentIndexChanged.connect(lambda _index: self.queue_changed.emit())
        self.new_campaign_button = QPushButton("Nueva campaña")
        self.new_campaign_button.clicked.connect(self.new_campaign_requested.emit)
        self.save_campaign_button = QPushButton("Guardar campaña")
        self.save_campaign_button.clicked.connect(self.save_campaign_requested.emit)
        self.delete_campaign_button = QPushButton("Eliminar campaña")
        self.delete_campaign_button.clicked.connect(self.delete_campaign_requested.emit)
        campaigns.addWidget(QLabel("Campaña"))
        campaigns.addWidget(self.campaign_combo, 1)
        campaigns.addWidget(self.campaign_name_edit, 1)
        campaigns.addWidget(self.execution_mode_combo)
        campaigns.addWidget(self.new_campaign_button)
        campaigns.addWidget(self.save_campaign_button)
        campaigns.addWidget(self.delete_campaign_button)
        layout.addLayout(campaigns)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(self.table)

        controls = QHBoxLayout()
        self.remove_button = QPushButton("Quitar seleccionado")
        self.remove_button.clicked.connect(self._remove_selected)
        self.start_button = QPushButton("Iniciar cola")
        self.start_button.clicked.connect(self.start_requested.emit)
        self.pause_button = QPushButton("Pausar después del actual")
        self.pause_button.clicked.connect(self.pause_requested.emit)
        self.pause_button.setEnabled(False)
        retry = QPushButton("Reintentar fallidos")
        retry.clicked.connect(self.retry_failed_requested.emit)
        clear = QPushButton("Limpiar completados")
        clear.clicked.connect(self.clear_completed_requested.emit)
        self.estimate_button = QPushButton("Estimar costos")
        self.estimate_button.clicked.connect(self.estimate_requested.emit)
        self.total_price_label = QLabel("Total estimado: no calculado")
        self.retry_failed_button = retry
        self.clear_completed_button = clear
        for button in (self.remove_button, self.start_button, self.pause_button, retry, clear, self.estimate_button):
            controls.addWidget(button)
        controls.addWidget(self.total_price_label)
        layout.addLayout(controls)
        self.status_label = QLabel("Cola vacía.")
        layout.addWidget(self.status_label)

    @staticmethod
    def _cell(value, *, editable=False) -> QTableWidgetItem:
        cell = QTableWidgetItem(str(value))
        if not editable:
            cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
        return cell

    def _append_row(self, item: FluxQueueItem) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        cells = [self._cell(row + 1), self._cell(", ".join(Path(name).name for name in item.image_paths)),
                 self._cell(item.prompt, editable=True), self._cell(item.size, editable=True),
                 self._cell(item.seed, editable=True),
                 self._cell("-" if item.price is None else f"${item.price:.4f}"),
                 self._cell(item.status), self._cell(item.task_id), self._cell(item.output_file),
                 self._cell(item.error)]
        cells[6].setData(Qt.UserRole, item.item_id)
        cells[1].setToolTip("\n".join(item.image_paths))
        self.table.blockSignals(True)
        for column, cell in enumerate(cells):
            self.table.setItem(row, column, cell)
        self.table.blockSignals(False)
        retry = QPushButton("Reintentar")
        retry.setEnabled(item.status == QueueStatus.FAILED)
        retry.clicked.connect(lambda _checked=False, item_id=item.item_id: self.retry_requested.emit(item_id))
        self.table.setCellWidget(row, 10, retry)

    def _item_changed(self, cell: QTableWidgetItem) -> None:
        if cell.column() in {2, 3, 4}:
            self._sync_item(cell.row())
            self.queue_changed.emit()

    def _sync_item(self, row: int) -> None:
        item_id = self.table.item(row, 6).data(Qt.UserRole)
        item = next((value for value in self._queue.items if value.item_id == item_id), None)
        if item is None:
            return
        item.prompt = self.table.item(row, 2).text()
        item.size = self.table.item(row, 3).text()
        try:
            item.seed = int(self.table.item(row, 4).text())
        except ValueError:
            pass

    def _remove_selected(self) -> None:
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            item_id = self.table.item(row, 6).data(Qt.UserRole)
            self._queue.items = [item for item in self._queue.items if item.item_id != item_id]
            self.table.removeRow(row)
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setText(str(row + 1))
        self.queue_changed.emit()
        self.status_label.setText(f"{len(self._queue.items)} ítem(s) en cola.")

    def load_queue(self, queue: FluxQueue) -> None:
        self._queue = queue
        self.execution_mode_combo.blockSignals(True)
        index = self.execution_mode_combo.findData(queue.execution_mode)
        self.execution_mode_combo.setCurrentIndex(max(0, index))
        self.execution_mode_combo.blockSignals(False)
        self.table.setRowCount(0)
        for item in queue.items:
            self._append_row(item)
        self.status_label.setText(f"{len(queue.items)} ítem(s) en cola.")

    def queue(self) -> FluxQueue:
        for row in range(self.table.rowCount()):
            self._sync_item(row)
        self._queue.execution_mode = str(self.execution_mode_combo.currentData() or "sequential")
        return self._queue

    def set_item_update(self, updated: FluxQueueItem) -> None:
        for row in range(self.table.rowCount()):
            if self.table.item(row, 6).data(Qt.UserRole) != updated.item_id:
                continue
            self.table.blockSignals(True)
            for column, value in ((5, "-" if updated.price is None else f"${updated.price:.4f}"),
                                  (6, updated.status), (7, updated.task_id),
                                  (8, updated.output_file), (9, updated.error)):
                self.table.item(row, column).setText(str(value))
            self.table.blockSignals(False)
            self.table.cellWidget(row, 10).setEnabled(updated.status == QueueStatus.FAILED)
            return

    def set_campaigns(self, campaigns: list, active_id: str | None) -> None:
        self.campaign_combo.blockSignals(True)
        self.campaign_combo.clear()
        for campaign in campaigns:
            self.campaign_combo.addItem(campaign.name, campaign.campaign_id)
        index = self.campaign_combo.findData(active_id)
        self.campaign_combo.setCurrentIndex(max(0, index))
        self.campaign_combo.blockSignals(False)

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.pause_button.setEnabled(running)
        for widget in (self.campaign_combo, self.campaign_name_edit, self.execution_mode_combo,
                       self.new_campaign_button, self.save_campaign_button, self.delete_campaign_button,
                       self.table, self.remove_button, self.retry_failed_button,
                       self.clear_completed_button, self.estimate_button):
            widget.setEnabled(not running)

    def set_total_price(self, total: float | None, error: str = "") -> None:
        if error:
            self.total_price_label.setText(f"Total estimado: error: {error}")
        elif total is None:
            self.total_price_label.setText("Total estimado: no calculado")
        else:
            self.total_price_label.setText(f"Total estimado: ${total:.4f} USD")
