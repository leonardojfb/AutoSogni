from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from app.sogni.loras import validate_lora_selection


class SogniLoraDialog(QDialog):
    def __init__(self, client, model_id: str, selected_loras: list[list], parent=None, *, show_personal=False) -> None:
        super().__init__(parent)
        self.client = client
        self.model_id = model_id
        self.selected_loras = [list(item) for item in selected_loras]
        self.show_personal = show_personal
        self.catalog: list[dict] = []
        self.setWindowTitle("MiniMax H3 LoRAs")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Seleccioná hasta 8 LoRAs. El orden de las filas es el orden de aplicación."))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Buscar por nombre, efecto o creador")
        self.search_edit.textChanged.connect(self._filter)
        layout.addWidget(self.search_edit)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["LoRA", "Fuerza", "Descripción", "Posición"])
        self.table.setColumnWidth(0, 210)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 420)
        self.table.setColumnWidth(3, 65)
        self.table.itemChanged.connect(lambda _item: self._update_positions())
        layout.addWidget(self.table)
        order = QHBoxLayout()
        up = QPushButton("↑ Subir")
        down = QPushButton("↓ Bajar")
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        order.addWidget(up)
        order.addWidget(down)
        order.addStretch()
        layout.addLayout(order)

        if show_personal:
            import_form = QFormLayout()
            self.import_url = QLineEdit()
            self.import_url.setPlaceholderText("https://huggingface.co/.../style.safetensors")
            self.import_name = QLineEdit()
            self.rights_check = QCheckBox("Tengo permiso para usar este LoRA en Sogni")
            import_form.addRow("Importar URL", self.import_url)
            import_form.addRow("Nombre", self.import_name)
            import_form.addRow("Derechos", self.rights_check)
            layout.addLayout(import_form)
            import_button = QPushButton("Importar a My LoRAs")
            import_button.clicked.connect(self._import)
            layout.addWidget(import_button)
        else:
            layout.addWidget(QLabel("My LoRAs se muestra al desactivar el filtro sensible en la campaña."))

        buttons = QHBoxLayout()
        refresh = QPushButton("Actualizar catálogo")
        save = QPushButton("Usar selección")
        cancel = QPushButton("Cancelar")
        refresh.clicked.connect(self.refresh)
        save.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(refresh)
        buttons.addStretch()
        buttons.addWidget(save)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        if self.catalog:
            self.selected_loras = self._checked_selection()
        try:
            self.catalog = self.client.fetch_loras(self.model_id, include_personal=self.show_personal)
        except Exception as exc:
            if not self.show_personal:
                QMessageBox.warning(self, "LoRAs", f"No se pudo cargar el catálogo: {exc}")
                return
            try:
                self.catalog = self.client.fetch_loras(self.model_id)
            except Exception:
                QMessageBox.warning(self, "LoRAs", f"No se pudo cargar el catálogo: {exc}")
                return
            QMessageBox.warning(self, "My LoRAs", f"No se pudo cargar My LoRAs; se muestra el catálogo público. {exc}")
        if not self.show_personal:
            self.catalog = [row for row in self.catalog if not (row.get("ui") or {}).get("nsfw") and not (row.get("ui") or {}).get("sexual")]
        selected = {item[0]: item[1] for item in self.selected_loras}
        rows = sorted(self.catalog, key=lambda row: (
            0 if row.get("loraId") in selected else 1,
            list(selected).index(row.get("loraId")) if row.get("loraId") in selected else 0,
        ))
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row in rows:
            row_index = self.table.rowCount()
            self.table.insertRow(row_index)
            lora_id = row["loraId"]
            item = QTableWidgetItem(row.get("name") or lora_id)
            item.setData(Qt.UserRole, lora_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if lora_id in selected else Qt.Unchecked)
            ui = row.get("ui") or {}
            details = QTableWidgetItem(row.get("description", "").replace("\n", " "))
            details.setToolTip(row.get("description", ""))
            strength = QDoubleSpinBox()
            strength.setDecimals(2)
            strength.setSingleStep(float(ui.get("step") or 0.05))
            strength.setRange(max(0.01, float(ui.get("min", 0))), min(1.0, float(ui.get("max", 1))) if lora_id.startswith("personal-") else float(ui.get("max", 1)))
            strength.setValue(float(selected.get(lora_id, ui.get("default", 1))))
            self.table.setItem(row_index, 0, item)
            self.table.setCellWidget(row_index, 1, strength)
            self.table.setItem(row_index, 2, details)
        self.table.blockSignals(False)
        self._update_positions()
        self._filter(self.search_edit.text())

    def _filter(self, query: str) -> None:
        query = query.casefold().strip()
        for row in range(self.table.rowCount()):
            text = " ".join((self.table.item(row, col).text() for col in (0, 2))).casefold()
            self.table.setRowHidden(row, query not in text)

    def _move(self, direction: int) -> None:
        row = self.table.currentRow()
        target = row + direction
        if row < 0 or target < 0 or target >= self.table.rowCount():
            return
        for col in (0, 2):
            first, second = self.table.takeItem(row, col), self.table.takeItem(target, col)
            self.table.setItem(row, col, second)
            self.table.setItem(target, col, first)
        first, second = self.table.cellWidget(row, 1), self.table.cellWidget(target, 1)
        first_value, second_value = first.value(), second.value()
        first.setValue(second_value)
        second.setValue(first_value)
        self.table.selectRow(target)
        self._update_positions()

    def _update_positions(self) -> None:
        position = 1
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                lora = self.table.item(row, 0)
                item = QTableWidgetItem(f"#{position}" if lora and lora.checkState() == Qt.Checked else "—")
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, 3, item)
                if lora and lora.checkState() == Qt.Checked:
                    position += 1
        finally:
            self.table.blockSignals(False)

    def _import(self) -> None:
        try:
            result = self.client.import_personal_lora(
                self.import_url.text().strip(), self.import_name.text().strip(),
                self.model_id, self.rights_check.isChecked(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Importar LoRA", str(exc))
            return
        QMessageBox.information(self, "Importar LoRA", f"Importación {result.get('status', 'en cola')}. Actualizá el catálogo cuando esté listo.")

    def _checked_selection(self) -> list[list]:
        selection = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item.checkState() == Qt.Checked:
                selection.append([item.data(Qt.UserRole), self.table.cellWidget(row, 1).value()])
        return selection

    def accept(self) -> None:
        try:
            self.selected_loras = validate_lora_selection(self.model_id, self._checked_selection(), self.catalog)
        except ValueError as exc:
            QMessageBox.warning(self, "LoRAs", str(exc))
            return
        super().accept()
