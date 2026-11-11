from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from app.ui.numeric_inputs import WheelSafeComboBox, WheelSafeDoubleSpinBox, WheelSafeSpinBox


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


def test_numeric_inputs_consume_mouse_wheel_without_changing_values():
    app = QApplication.instance() or QApplication([])
    for spin_box, value in (
        (WheelSafeSpinBox(), 5),
        (WheelSafeDoubleSpinBox(), 0.5),
    ):
        spin_box.setRange(0, 10)
        spin_box.setValue(value)
        event = _wheel_event()
        app.sendEvent(spin_box, event)
        assert spin_box.value() == value
        assert event.isAccepted()
        assert spin_box.lineEdit().isReadOnly() is False


def test_combo_box_consumes_mouse_wheel_without_changing_selection():
    app = QApplication.instance() or QApplication([])
    combo = WheelSafeComboBox()
    combo.addItems(["first", "second", "third"])
    combo.setCurrentIndex(1)
    event = _wheel_event()
    app.sendEvent(combo, event)
    assert combo.currentIndex() == 1
    assert event.isAccepted()
    combo.setCurrentIndex(2)
    assert combo.currentIndex() == 2
