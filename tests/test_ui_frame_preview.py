import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.database.db import Database
from app.database.repositories import CampaignRepository
from app.sogni.auth import ApiKeyStore
from app.sogni.client import SogniClient
from app.ui.main_window import MainWindow


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
