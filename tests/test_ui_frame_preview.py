import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem

from app.database.db import Database
from app.database.repositories import CampaignRepository
from app.sogni.auth import ApiKeyStore
from app.sogni.client import SogniClient
from app.ui.main_window import MainWindow
from app.wavespeed.queue import (
    QueueStatus,
    WaveSpeedCampaign,
    WaveSpeedCampaignStore,
    WaveSpeedQueue,
    WaveSpeedQueueItem,
    WaveSpeedQueueStore,
)


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
    assert window.wavespeed_queue_widget.table.item(0, 9).text() == "-"
    assert window.wavespeed_queue_widget.estimate_button.text() == "Estimar costos"
    assert window.wavespeed_queue_widget.total_price_label.text() == "Total estimado: no calculado"
    assert window.wavespeed_queue_estimate_button.text() == "Estimar costos cola"
    assert window.wavespeed_queue_total_label.text() == "Total cola: no estimado"


def test_wavespeed_image_panel_exposes_add_to_queue_action(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)

    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"))

    assert window.wavespeed_queue_add_button.text() == "Agregar a la cola"


def test_add_to_queue_groups_jobs_by_frame_name(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    campaign_store = WaveSpeedCampaignStore(tmp_path / "campaigns.json")
    queue_store = WaveSpeedQueueStore(tmp_path / "queue.json")
    window = MainWindow(
        repo,
        SogniClient(""),
        ApiKeyStore(tmp_path / "key.txt"),
        wavespeed_campaign_store=campaign_store,
        wavespeed_queue_store=queue_store,
    )
    window.wavespeed_queue_widget.load_queue(WaveSpeedQueue())

    def add_reference(kind: str, path: str):
        item = QListWidgetItem(Path(path).name)
        item.setData(Qt.UserRole, path)
        window.wavespeed_reference_lists[kind].addItem(item)

    add_reference("reference_images", "C:/frames/frame-a.png")
    add_reference("reference_videos", "C:/videos/video-1.mp4")
    window._wavespeed_add_current_to_queue()
    add_reference("reference_videos", "C:/videos/video-2.mp4")
    window._wavespeed_add_current_to_queue()

    queue = window.wavespeed_queue_widget.queue()
    assert len(queue.items) == 2
    assert all(Path(item.video_path).name.startswith("video-") for item in queue.items)
    assert all(Path(item.video_path).name for item in queue.items)
    assert Path(queue.frame_path).name == "frame-a.png"
    campaigns, _active_id = campaign_store.load()
    assert len(campaigns[0].queue.items) == 2
    assert Path(campaigns[0].queue.frame_path).name == "frame-a.png"

    window.wavespeed_reference_lists["reference_images"].clear()
    add_reference("reference_images", "C:/frames/frame-b.png")
    add_reference("reference_videos", "C:/videos/video-3.mp4")
    window._wavespeed_add_current_to_queue()

    assert len(window.wavespeed_queue_widget.queue().items) == 2
    assert "distinto" in window.wavespeed_status_label.text()


def test_wavespeed_campaign_controls_are_visible(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"))

    assert window.wavespeed_campaign_combo is not None
    assert window.wavespeed_campaign_name_edit is not None
    assert window.wavespeed_campaign_save_button.text() == "Guardar campaña"
    assert window.wavespeed_campaign_new_button.text() == "Nueva campaña"
    assert window.wavespeed_execution_mode_combo.currentData() == "sequential"
    assert window.wavespeed_execution_mode_combo.findData("parallel") >= 0


def test_wavespeed_campaign_survives_window_reopen(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    campaign_store = WaveSpeedCampaignStore(tmp_path / "campaigns.json")
    queue_store = WaveSpeedQueueStore(tmp_path / "legacy-queue.json")

    first = MainWindow(
        repo,
        SogniClient(""),
        ApiKeyStore(tmp_path / "key.txt"),
        wavespeed_campaign_store=campaign_store,
        wavespeed_queue_store=queue_store,
    )
    first.wavespeed_queue_widget.set_campaign_name("Frame azul")
    first.wavespeed_queue_widget.set_frame_path("C:/frames/frame-a.png")
    first.wavespeed_queue_widget.add_video("C:/videos/video-a.mp4")
    first._save_wavespeed_campaign()

    second = MainWindow(
        repo,
        SogniClient(""),
        ApiKeyStore(tmp_path / "key-2.txt"),
        wavespeed_campaign_store=campaign_store,
        wavespeed_queue_store=queue_store,
    )

    assert second.wavespeed_campaign_name_edit.text() == "Frame azul"
    assert second.wavespeed_queue_widget.queue().frame_path.endswith("frame-a.png")
    assert second.wavespeed_queue_widget.queue().items[0].video_path.endswith("video-a.mp4")


def test_empty_active_campaign_recovers_existing_legacy_queue(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    campaign_store = WaveSpeedCampaignStore(tmp_path / "campaigns.json")
    queue_store = WaveSpeedQueueStore(tmp_path / "legacy-queue.json")
    empty_campaign = WaveSpeedCampaign(name="Campaña importada")
    campaign_store.save(empty_campaign, active=True)
    legacy_queue = WaveSpeedQueue(
        frame_path="C:/frames/recovered.png",
        items=[WaveSpeedQueueItem(video_path="C:/videos/recovered.mp4", prompt="Recovered prompt")],
    )
    queue_store.save(legacy_queue)

    window = MainWindow(
        repo,
        SogniClient(""),
        ApiKeyStore(tmp_path / "key.txt"),
        wavespeed_campaign_store=campaign_store,
        wavespeed_queue_store=queue_store,
    )

    recovered = window.wavespeed_queue_widget.queue()
    assert recovered.frame_path.endswith("recovered.png")
    assert recovered.items[0].prompt == "Recovered prompt"
    assert campaign_store.load()[0][0].queue.items[0].video_path.endswith("recovered.mp4")


def test_new_empty_campaign_does_not_recover_stale_legacy_queue(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    campaign_store = WaveSpeedCampaignStore(tmp_path / "campaigns.json")
    queue_store = WaveSpeedQueueStore(tmp_path / "legacy-queue.json")
    saved_campaign = WaveSpeedCampaign(
        name="Guardada",
        queue=WaveSpeedQueue(
            frame_path="C:/frames/saved.png",
            items=[WaveSpeedQueueItem(video_path="C:/videos/saved.mp4", prompt="Saved")],
        ),
    )
    campaign_store.save(saved_campaign, active=False)
    new_campaign = WaveSpeedCampaign(name="Nueva campaña")
    campaign_store.save(new_campaign, active=True)
    queue_store.save(
        WaveSpeedQueue(
            frame_path="C:/frames/stale.png",
            items=[WaveSpeedQueueItem(video_path="C:/videos/stale.mp4", prompt="Stale")],
        )
    )

    window = MainWindow(
        repo,
        SogniClient(""),
        ApiKeyStore(tmp_path / "key.txt"),
        wavespeed_campaign_store=campaign_store,
        wavespeed_queue_store=queue_store,
    )

    assert window.wavespeed_queue_widget.queue().items == []


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
