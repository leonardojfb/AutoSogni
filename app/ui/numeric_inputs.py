from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSpinBox


class WheelSafeComboBox(QComboBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class WheelSafeSpinBox(QSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class WheelSafeDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()
