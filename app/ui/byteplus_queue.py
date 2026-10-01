from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from app.byteplus.queue import BytePlusQueue

class BytePlusQueueWidget(QWidget):
    start_requested = Signal()
    queue_changed = Signal()
    def __init__(self, parent=None):
        super().__init__(parent); self._queue = BytePlusQueue(); layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Cola secuencial BytePlus · cada fila conserva su solicitud Seedance 2.0"))
        self.table = QTableWidget(0, 4); self.table.setHorizontalHeaderLabels(["#", "Prompt", "Estado", "Task ID"]); layout.addWidget(self.table)
        row = QHBoxLayout(); self.start_button = QPushButton("Iniciar cola"); self.start_button.clicked.connect(self.start_requested.emit); row.addWidget(self.start_button); layout.addLayout(row)
    def load_queue(self, queue):
        self._queue = queue; self.table.setRowCount(0)
        for item in queue.items: self.add_item(item)
    def add_item(self, item):
        row = self.table.rowCount(); self.table.insertRow(row)
        for column, value in enumerate((row + 1, item.snapshot.get("prompt", ""), item.status, item.task_id)): self.table.setItem(row, column, QTableWidgetItem(str(value)))
    def queue(self): return self._queue
