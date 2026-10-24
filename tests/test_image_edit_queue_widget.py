from PySide6.QtWidgets import QApplication

from app.ui.image_edit_queue import ImageEditQueueWidget
from app.wavespeed.image_edit_queue import ImageEditQueue, ImageEditQueueItem


def _app():
    return QApplication.instance() or QApplication([])


def test_widget_displays_settings_and_disables_mutation_while_running():
    _app()
    widget = ImageEditQueueWidget("Nueva campaña Qwen", "Opciones Qwen")
    item = ImageEditQueueItem(prompt="keep face", image_paths=["C:/one.png"],
                              settings={"resolution": "2k", "seed": 42})

    widget.load_queue(ImageEditQueue(items=[item]))

    assert widget.table.item(0, widget.SETTINGS_COLUMN).text() == "resolution=2k; seed=42"
    widget.set_running(True)
    assert not widget.start_button.isEnabled()
    assert not widget.estimate_button.isEnabled()
