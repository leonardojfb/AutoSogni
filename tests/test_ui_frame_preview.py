import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.database.db import Database
from app.database.repositories import CampaignRepository
from app.sogni.auth import ApiKeyStore
from app.sogni.client import SogniClient
from app.ui.main_window import MainWindow
from app.wavespeed.queue import QueueStatus, WaveSpeedQueue, WaveSpeedQueueItem, WaveSpeedQueueStore


def _app():
    return QApplication.instance() or QApplication([])


def test_clicking_frame_row_loads_thumbnail_preview(tmp_path: Path):
    app = _app()
    from PySide6.QtGui import QImage

    image_path = tmp_path / "frame.png"
    image = QImage(24, 32, QImage.Format_RGB32)
    image.fill(0x336699)
    assert image.save(str(image_path))

    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    campaign_id = repo.insert_campaign(
        {
            "name": "Preview Campaign",
            "status": "READY",
            "model_id": "wan22",
            "model_name": "WAN 2.2",
            "frames_folder": str(tmp_path),
            "prompts_source": "",
            "output_folder": str(tmp_path / "out"),
            "filename_template": "{outfit}__{prompt_id}_{prompt_name}.mp4",
            "organization_mode": "flat",
            "concurrency": 1,
            "settings_json": "{}",
        }
    )
    repo.insert_frame(campaign_id, str(image_path), image_path.name, "Blue Test", "sha")

    window = MainWindow(repo, SogniClient(""), ApiKeyStore(Path(tempfile.mkdtemp()) / "key.txt"))
    window.current_campaign_id = campaign_id
    window._refresh_tables()

    window.frames_table.selectRow(0)
    window._frame_selected()

    assert window.frame_preview_label.pixmap() is not None
    assert "frame.png" in window.frame_preview_meta.text()
    assert "Blue Test" in window.frame_preview_meta.text()
    assert "WaveSpeed" in [window.tabs.tabText(index) for index in range(window.tabs.count())]
    assert window.wavespeed.MODEL_ID == "alibaba/wan-3.0/reference-to-video"


def test_wavespeed_event_handler_belongs_to_main_window(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)

    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"))

    assert "_handle_wavespeed_event" in type(window).__dict__


def test_wavespeed_queue_panel_adds_rows_and_exposes_retry(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)

    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"))
    window.wavespeed_queue_widget.load_queue(WaveSpeedQueue(frame_path="frame.png", items=[]))
    window.wavespeed_queue_widget.add_item_for_test("video.mp4")

    item = window.wavespeed_queue_widget.queue().items[0]
    assert item.video_path == "video.mp4"
    assert item.status == QueueStatus.PENDING
    assert window.wavespeed_queue_widget.has_retry_control(item.item_id)
    assert window.wavespeed_queue_widget.total_price_label.text() == "Total estimado: no calculado"
    assert window.wavespeed_queue_widget.estimate_button.text() == "Estimar costos"


def test_wavespeed_image_panel_exposes_add_to_queue_action(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)

    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"))

    assert any(
        button.text() == "Agregar a la cola"
        for button in window.findChildren(__import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton)
    )


def test_wavespeed_queue_event_updates_row_and_retry_button(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)

    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"))
    window.wavespeed_queue_store = WaveSpeedQueueStore(tmp_path / "queue.json")
    window.wavespeed_queue_widget.load_queue(WaveSpeedQueue())
    item = window.wavespeed_queue_widget.add_item_for_test("video.mp4")
    item.status = QueueStatus.FAILED
    item.task_id = "pred-1"
    item.error = "bad video"

    window._handle_wavespeed_event("queue_item", item)

    assert window.wavespeed_queue_widget.table.item(0, 10).text() == QueueStatus.FAILED
    assert window.wavespeed_queue_widget.table.item(0, 11).text() == "pred-1"
    assert window.wavespeed_queue_widget.table.item(0, 13).text() == "bad video"
    assert window.wavespeed_queue_widget.table.cellWidget(0, 14).isEnabled()
