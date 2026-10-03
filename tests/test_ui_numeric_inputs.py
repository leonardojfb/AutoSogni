from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from app.ui.numeric_inputs import WheelSafeDoubleSpinBox, WheelSafeSpinBox


def _wheel_event() -> QWheelEvent:
    return QWheelEvent(
        QPointF(1, 1),
        QPointF(1, 1),
        QPoint(),
        QPoint(0, 120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollUpdate,
        False,
    )


def test_numeric_inputs_ignore_mouse_wheel_and_remain_editable():
    app = QApplication.instance() or QApplication([])
    for spin_box, value in (
        (WheelSafeSpinBox(), 5),
        (WheelSafeDoubleSpinBox(), 0.5),
    ):
        spin_box.setRange(0, 10)
        spin_box.setValue(value)
        app.sendEvent(spin_box, _wheel_event())
        assert spin_box.value() == value
        assert spin_box.lineEdit().isReadOnly() is False