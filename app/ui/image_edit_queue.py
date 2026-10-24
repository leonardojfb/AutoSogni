from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from app.wavespeed.image_edit_queue import ImageEditQueue, ImageEditQueueItem
from app.wavespeed.queue import QueueStatus


class ImageEditQueueWidget(QWidget):
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

    COLUMNS = ("#", "Imágenes", "Prompt", "Opciones", "Precio", "Estado", "Task", "Archivo", "Error", "Reintentar")
    SETTINGS_COLUMN = 3

    def __init__(self, default_campaign_name: str, settings_label: str, parent=None) -> None:
        super().__init__(parent)
        self._queue = ImageEditQueue()
        layout = QVBoxLayout(self)
        campaigns = QHBoxLayout()
        self.campaign_combo, self.campaign_name_edit = QComboBox(), QLineEdit(default_campaign_name)
        self.campaign_combo.currentIndexChanged.connect(lambda index: self.campaign_selected.emit(
            str(self.campaign_combo.itemData(index) or "")) if index >= 0 else None)
        self.new_campaign_button, self.save_campaign_button = QPushButton("Nueva campaña"), QPushButton("Guardar campaña")
        self.delete_campaign_button = QPushButton("Eliminar campaña")
        self.new_campaign_button.clicked.connect(self.new_campaign_requested.emit)
        self.save_campaign_button.clicked.connect(self.save_campaign_requested.emit)
        self.delete_campaign_button.clicked.connect(self.delete_campaign_requested.emit)
        for widget in (QLabel("Campaña"), self.campaign_combo, self.campaign_name_edit,
                       self.new_campaign_button, self.save_campaign_button, self.delete_campaign_button):
            campaigns.addWidget(widget, 1 if widget in {self.campaign_combo, self.campaign_name_edit} else 0)
        layout.addLayout(campaigns)
        layout.addWidget(QLabel(settings_label))
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(self.table)
        controls = QHBoxLayout()
        self.remove_button, self.start_button = QPushButton("Quitar seleccionado"), QPushButton("Iniciar cola")
        self.pause_button, self.retry_failed_button = QPushButton("Pausar después del actual"), QPushButton("Reintentar fallidos")
        self.clear_completed_button, self.estimate_button = QPushButton("Limpiar completados"), QPushButton("Estimar costos")
        self.pause_button.setEnabled(False)
        self.remove_button.clicked.connect(self._remove_selected)
        self.start_button.clicked.connect(self.start_requested.emit)
        self.pause_button.clicked.connect(self.pause_requested.emit)
        self.retry_failed_button.clicked.connect(self.retry_failed_requested.emit)
        self.clear_completed_button.clicked.connect(self.clear_completed_requested.emit)
        self.estimate_button.clicked.connect(self.estimate_requested.emit)
        self.total_price_label, self.status_label = QLabel("Total estimado: no calculado"), QLabel("Cola vacía.")
        for widget in (self.remove_button, self.start_button, self.pause_button, self.retry_failed_button,
                       self.clear_completed_button, self.estimate_button, self.total_price_label):
            controls.addWidget(widget)
        layout.addLayout(controls); layout.addWidget(self.status_label)

    @staticmethod
    def _cell(value, editable=False):
        cell = QTableWidgetItem(str(value))
        if not editable:
            cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
        return cell

    def _append_row(self, item: ImageEditQueueItem) -> None:
        row = self.table.rowCount(); self.table.insertRow(row)
        settings = "; ".join(f"{key}={value}" for key, value in sorted(item.settings.items()))
        cells = [self._cell(row + 1), self._cell(", ".join(Path(name).name for name in item.image_paths)),
                 self._cell(item.prompt, True), self._cell(settings),
                 self._cell("-" if item.price is None else f"${item.price:.4f}"), self._cell(item.status),
                 self._cell(item.task_id), self._cell(item.output_file), self._cell(item.error)]
        cells[5].setData(Qt.UserRole, item.item_id); cells[1].setToolTip("\n".join(item.image_paths))
        self.table.blockSignals(True)
        for column, cell in enumerate(cells): self.table.setItem(row, column, cell)
        self.table.blockSignals(False)
        retry = QPushButton("Reintentar"); retry.setEnabled(item.status == QueueStatus.FAILED)
        retry.clicked.connect(lambda _checked=False, item_id=item.item_id: self.retry_requested.emit(item_id))
        self.table.setCellWidget(row, 9, retry)

    def _item_changed(self, cell: QTableWidgetItem) -> None:
        if cell.column() == 2:
            self._sync_item(cell.row()); self.queue_changed.emit()

    def _sync_item(self, row: int) -> None:
        item_id = self.table.item(row, 5).data(Qt.UserRole)
        item = next((item for item in self._queue.items if item.item_id == item_id), None)
        if item: item.prompt = self.table.item(row, 2).text()

    def _remove_selected(self) -> None:
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            item_id = self.table.item(row, 5).data(Qt.UserRole)
            self._queue.items = [item for item in self._queue.items if item.item_id != item_id]; self.table.removeRow(row)
        self.queue_changed.emit()

    def load_queue(self, queue: ImageEditQueue) -> None:
        self._queue = queue; self.table.setRowCount(0)
        for item in queue.items: self._append_row(item)
        self.status_label.setText(f"{len(queue.items)} ítem(s) en cola.")

    def queue(self) -> ImageEditQueue:
        for row in range(self.table.rowCount()): self._sync_item(row)
        return self._queue

    def set_item_update(self, updated: ImageEditQueueItem) -> None:
        for row in range(self.table.rowCount()):
            if self.table.item(row, 5).data(Qt.UserRole) == updated.item_id:
                for column, value in ((4, "-" if updated.price is None else f"${updated.price:.4f}"),
                                      (5, updated.status), (6, updated.task_id), (7, updated.output_file), (8, updated.error)):
                    self.table.item(row, column).setText(str(value))
                self.table.cellWidget(row, 9).setEnabled(updated.status == QueueStatus.FAILED); return

    def set_campaigns(self, campaigns: list, active_id: str | None) -> None:
        self.campaign_combo.blockSignals(True); self.campaign_combo.clear()
        for campaign in campaigns: self.campaign_combo.addItem(campaign.name, campaign.campaign_id)
        self.campaign_combo.setCurrentIndex(max(0, self.campaign_combo.findData(active_id))); self.campaign_combo.blockSignals(False)

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running); self.pause_button.setEnabled(running)
        for widget in (self.campaign_combo, self.campaign_name_edit, self.new_campaign_button, self.save_campaign_button,
                       self.delete_campaign_button, self.table, self.remove_button, self.retry_failed_button,
                       self.clear_completed_button, self.estimate_button): widget.setEnabled(not running)

    def set_total_price(self, total: float | None, error: str = "") -> None:
        self.total_price_label.setText(f"Total estimado: error: {error}" if error else
                                       "Total estimado: no calculado" if total is None else f"Total estimado: ${total:.4f} USD")
