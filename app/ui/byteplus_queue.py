from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from app.byteplus.queue import BytePlusQueue

class BytePlusQueueWidget(QWidget):
    start_requested = Signal()
    estimate_requested = Signal()
    queue_changed = Signal()
    def __init__(self, parent=None):
        super().__init__(parent); self._queue = BytePlusQueue(); layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Cola secuencial BytePlus · cada fila conserva su solicitud Seedance 2.0"))
        self.table = QTableWidget(0, 5); self.table.setHorizontalHeaderLabels(["#", "Prompt", "Precio", "Estado", "Task ID"]); layout.addWidget(self.table)
        row = QHBoxLayout(); self.start_button = QPushButton("Iniciar cola"); self.start_button.clicked.connect(self.start_requested.emit); row.addWidget(self.start_button)
        self.estimate_button = QPushButton("Estimar costos"); self.estimate_button.clicked.connect(self.estimate_requested.emit); row.addWidget(self.estimate_button)
        self.total_price_label = QLabel("Total estimado: no calculado"); row.addWidget(self.total_price_label); layout.addLayout(row)
    def load_queue(self, queue):
        self._queue = queue; self.table.setRowCount(0)
        for item in queue.items: self.add_item(item)
    def add_item(self, item):
        row = self.table.rowCount(); self.table.insertRow(row)
        for column, value in enumerate((row + 1, item.snapshot.get("prompt", ""), "-" if item.price is None else f"${item.price:.4f}", item.status, item.task_id)):
            self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.table.item(row, 3).setData(Qt.UserRole, item.item_id)
    def queue(self): return self._queue

    def set_item_price(self, item_id: str, price: float | None) -> None:
        item = next((value for value in self._queue.items if value.item_id == item_id), None)
        if item is not None:
            item.price = price
        for row in range(self.table.rowCount()):
            if self.table.item(row, 3).data(Qt.UserRole) == item_id:
                self.table.item(row, 2).setText("-" if price is None else f"${price:.4f}")
                break

    def set_total_price(self, total: float | None, error: str = "") -> None:
        if error:
            self.total_price_label.setText(f"Total estimado: error: {error}")
        elif total is None:
            self.total_price_label.setText("Total estimado: no calculado")
        else:
            self.total_price_label.setText(f"Total estimado: ${total:.4f} USD")
