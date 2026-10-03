from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QGroupBox, QHBoxLayout,
                              QInputDialog, QLabel, QListWidget, QListWidgetItem,
                              QMessageBox, QPushButton, QVBoxLayout, QWidget)

from app.byteplus.media import FORMATS, validate_file


class BytePlusReferences(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.hint = QLabel()
        layout.addWidget(self.hint)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.lists, self.boxes = {}, {}
        for kind, title in (("image", "Imágenes"), ("video", "Videos"), ("audio", "Audios")):
            box = QGroupBox(title)
            self.boxes[kind] = box
            column = QVBoxLayout(box)
            items = QListWidget()
            items.setMinimumHeight(95)
            items.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.lists[kind] = items
            column.addWidget(items)
            buttons = QHBoxLayout()
            for label, callback in (
                ("Agregar", lambda checked=False, k=kind: self.choose(k)),
                ("URL / asset", lambda checked=False, k=kind: self.add_url(k)),
                ("Quitar", lambda checked=False, k=kind: self.remove(k)),
                ("↑", lambda checked=False, k=kind: self.move(k, -1)),
                ("↓", lambda checked=False, k=kind: self.move(k, 1)),
            ):
                button = QPushButton(label)
                button.clicked.connect(callback)
                buttons.addWidget(button)
            column.addLayout(buttons)
            row.addWidget(box)
        self.set_mode(0)

    def choose(self, kind):
        extensions = " ".join("*" + suffix for suffix in sorted(FORMATS[kind]))
        paths, _ = QFileDialog.getOpenFileNames(self, "Seleccionar referencias", "", f"Archivos ({extensions})")
        for path in paths:
            try:
                validate_file(path, kind)
                self.add_reference(kind, path)
            except ValueError as exc:
                QMessageBox.warning(self, "Referencia inválida", str(exc))

    def add_reference(self, kind, value):
        if value in self.values(kind):
            return
        item = QListWidgetItem(value if "://" in value else Path(value).name)
        item.setData(Qt.UserRole, value)
        item.setToolTip(value)
        self.lists[kind].addItem(item)

    def add_url(self, kind):
        value, ok = QInputDialog.getText(self, "Referencia remota", "URL pública o asset://…")
        if ok and value.strip():
            if not value.strip().startswith(("https://", "http://", "asset://")):
                QMessageBox.warning(self, "Referencia inválida", "Ingresá una URL http(s) o asset://.")
                return
            self.add_reference(kind, value.strip())

    def values(self, kind):
        items = self.lists[kind]
        return [items.item(i).data(Qt.UserRole) for i in range(items.count())]

    def remove(self, kind):
        items = self.lists[kind]
        for item in items.selectedItems():
            items.takeItem(items.row(item))

    def move(self, kind, direction):
        items = self.lists[kind]
        source = items.currentRow()
        target = source + direction
        if source >= 0 and 0 <= target < items.count():
            items.insertItem(target, items.takeItem(source))
            items.setCurrentRow(target)

    def set_mode(self, index):
        self.boxes["image"].setVisible(index in (1, 2, 3))
        self.boxes["video"].setVisible(index in (3, 4, 5))
        self.boxes["audio"].setVisible(index == 3)
        self.hint.setText(("Texto a video: no usa referencias.", "Seleccioná una imagen inicial.",
                           "Imagen 1 = primer frame; imagen 2 = último frame. Usá ↑ / ↓ para ordenar.",
                           "Hasta 9 imágenes, 3 videos y 3 audios. El orden define Imagen 1, Video 1…",
                           "Seleccioná un video para editar.", "Seleccioná un video para extender.")[index])

    def snapshot(self, index):
        images, videos, audios = (self.values(k) for k in ("image", "video", "audio"))
        if index in (1, 2):
            if len(images) != index:
                raise ValueError(f"Seleccioná exactamente {index} imagen(es).")
            return {"first_frame_url": images[0], **({"last_frame_url": images[1]} if index == 2 else {})}
        if index == 3:
            return {"reference_images": images, "reference_videos": videos, "reference_audios": audios}
        if index in (4, 5):
            if len(videos) != 1:
                raise ValueError("Seleccioná exactamente un video.")
            return {"reference_video_url": videos[0]}
        return {}
