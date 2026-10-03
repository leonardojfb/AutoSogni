from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox


class WheelSafeSpinBox(QSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class WheelSafeDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()