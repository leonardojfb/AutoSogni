from pathlib import Path
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QListWidgetItem, QMessageBox

from app.core.campaign_manager import CampaignManager
from app.database.db import Database
from app.database.repositories import CampaignRepository
from app.sogni.auth import ApiKeyStore
from app.sogni.client import SogniClient
from app.sogni.schemas import ModelDescriptor
from app.ui.main_window import MainWindow
from app.wavespeed.queue import (
    QueueStatus,
    WaveSpeedCampaign,
    WaveSpeedCampaignStore,
    WaveSpeedQueue,
    WaveSpeedQueueItem,
    WaveSpeedQueueStore,
)
from app.wavespeed.validation import SEEDANCE_MODEL_ID, FLUX_MODEL_ID, FACE_SWAP_MODEL_ID
from app.wavespeed.flux_queue import FluxCampaignStore
from app.wavespeed.history import WaveSpeedHistoryStore
from app.wavespeed.schemas import WaveSpeedPrediction


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

    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"),
                        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
                        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "campaigns.json"),
                        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "queue.json"))
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

    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"),
                        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
                        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "campaigns.json"),
                        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "queue.json"))

    assert "_handle_wavespeed_event" in type(window).__dict__


def test_seedance_and_flux_tabs_build_model_specific_payloads(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    window = MainWindow(
        CampaignRepository(db), SogniClient(""), ApiKeyStore(tmp_path / "sogni-key.txt"),
        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "campaigns.json"),
        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "queue.json"),
    )
    window.wavespeed_model_combo.setCurrentIndex(1)
    window.wavespeed_prompt_edit.setPlainText("Seedance prompt")
    window.wavespeed_resolution_combo.setCurrentText("4k")
    window.wavespeed_aspect_combo.setCurrentText("21:9")
    window.wavespeed_duration_spin.setValue(15)
    payload, _ = window._wavespeed_upload_and_build(window._wavespeed_snapshot())
    assert window.wavespeed_model_combo.currentData() == SEEDANCE_MODEL_ID
    assert payload["prompt"] == "Seedance prompt"
    assert payload["resolution"] == "4k"
    assert "seed" not in payload and "enable_prompt_expansion" not in payload

    image = tmp_path / "source.png"
    image.write_bytes(b"png")
    entry = QListWidgetItem(image.name)
    entry.setData(Qt.UserRole, str(image))
    window.flux_images.addItem(entry)
    window.flux_prompt_edit.setPlainText("Flux prompt")
    window._wavespeed_upload_cached = lambda _path: {"download_url": "https://cdn/image.png"}
    flux_payload = window._flux_upload_and_build(window._flux_snapshot())
    assert FLUX_MODEL_ID == "wavespeed-ai/flux-2-klein-9b/edit"
    assert flux_payload["images"] == ["https://cdn/image.png"]
    assert flux_payload["prompt"] == "Flux prompt"

    target = tmp_path / "target.png"
    target.write_bytes(b"target")
    window.face_swap_target_edit.setText(str(target))
    window.face_swap_identity_edit.setText(str(image))
    face_swap_payload = window._face_swap_upload_and_build(window._face_swap_snapshot())
    assert FACE_SWAP_MODEL_ID == "wavespeed-ai/image-face-swap"
    assert face_swap_payload["image"] == "https://cdn/image.png"
    assert face_swap_payload["face_image"] == "https://cdn/image.png"
    assert "Imágenes" in [window.tabs.tabText(index) for index in range(window.tabs.count())]


def test_flux_reference_images_can_be_reordered(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    window = MainWindow(
        CampaignRepository(db), SogniClient(""), ApiKeyStore(tmp_path / "sogni-key.txt"),
        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "campaigns.json"),
        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "queue.json"),
    )
    for name in ("one.png", "two.png", "three.png"):
        image = tmp_path / name
        image.write_bytes(b"png")
        item = QListWidgetItem(name)
        item.setData(Qt.UserRole, str(image))
        window.flux_images.addItem(item)

    window.flux_images.setCurrentRow(1)
    window._flux_move_image(-1)

    assert [window.flux_images.item(index).text() for index in range(3)] == [
        "two.png", "one.png", "three.png",
    ]
    assert window.flux_images.currentRow() == 0


def test_flux_add_to_queue_persists_snapshot_in_its_own_campaign(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    store = FluxCampaignStore(tmp_path / "flux_campaigns.json")
    window = MainWindow(
        CampaignRepository(db), SogniClient(""), ApiKeyStore(tmp_path / "sogni-key.txt"),
        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "wavespeed_campaigns.json"),
        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "wavespeed_queue.json"),
        flux_campaign_store=store,
    )
    video_campaign_bytes = (tmp_path / "wavespeed_campaigns.json").read_bytes()
    image = tmp_path / "source.png"
    image.write_bytes(b"png")
    entry = QListWidgetItem(image.name)
    entry.setData(Qt.UserRole, str(image))
    window.flux_images.addItem(entry)
    window.flux_prompt_edit.setPlainText("Keep exact prompt")
    window.flux_size_edit.setText("1024*1024")
    window._flux_add_current_to_queue()
    window.flux_prompt_edit.setPlainText("Changed later")

    campaigns, active_id = store.load()
    assert active_id == campaigns[0].campaign_id
    assert len(campaigns[0].queue.items) == 1
    assert campaigns[0].queue.items[0].prompt == "Keep exact prompt"
    assert campaigns[0].queue.items[0].image_paths == [str(image)]
    assert campaigns[0].queue.items[0].size == "1024*1024"
    assert window.flux_queue_widget.queue().items[0].prompt == "Keep exact prompt"

    window._new_flux_campaign()
    assert len(store.load()[0]) == 2
    assert window.flux_queue_widget.queue().items == []
    window._select_flux_campaign(campaigns[0].campaign_id)
    assert window.flux_queue_widget.queue().items[0].prompt == "Keep exact prompt"
    assert (tmp_path / "wavespeed_campaigns.json").read_bytes() == video_campaign_bytes


def test_flux_queue_runs_from_tab_with_fake_client_and_persists_result(tmp_path: Path):
    app = _app()
    db = Database(tmp_path / "app.db")
    db.initialize()

    class Client:
        MODEL_ID = "alibaba/wan-3.0/reference-to-video"
        BASE_URL = "https://api.wavespeed.ai/api/v3"

        def upload_file(self, path):
            return {"download_url": f"https://cdn/{path.name}"}

        def estimate_price(self, payload, *, model_id):
            assert model_id == FLUX_MODEL_ID
            return {"price": 0.016}

        def submit(self, payload, *, model_id):
            assert model_id == FLUX_MODEL_ID
            assert payload["prompt"] == "Edit me"
            return WaveSpeedPrediction("flux-task", "completed", outputs=["https://cdn/result.png"])

        def download_output(self, _url, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"result")
            return destination

    store = FluxCampaignStore(tmp_path / "flux_campaigns.json")
    window = MainWindow(
        CampaignRepository(db), SogniClient(""), ApiKeyStore(tmp_path / "sogni-key.txt"),
        wavespeed_client=Client(), wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "wavespeed_campaigns.json"),
        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "wavespeed_queue.json"),
        flux_campaign_store=store,
    )
    window.wavespeed_history = WaveSpeedHistoryStore(tmp_path / "history.json")
    image = tmp_path / "input.png"
    image.write_bytes(b"input")
    entry = QListWidgetItem(image.name)
    entry.setData(Qt.UserRole, str(image))
    window.flux_images.addItem(entry)
    window.flux_prompt_edit.setPlainText("Edit me")
    window.flux_output_edit.setText(str(tmp_path / "outputs"))
    window._flux_add_current_to_queue()
    window._start_flux_queue()
    deadline = time.monotonic() + 5
    while window._flux_queue_running and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()

    assert not window._flux_queue_running
    item = store.load()[0][0].queue.items[0]
    assert item.status == QueueStatus.COMPLETED
    assert item.task_id == "flux-task"
    assert Path(item.output_file).read_bytes() == b"result"
    assert window.wavespeed_history.list()[0]["model_id"] == FLUX_MODEL_ID


def test_wavespeed_queue_panel_adds_rows_and_exposes_retry(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)

    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"),
                        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
                        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "campaigns.json"),
                        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "queue.json"))
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

    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"),
                        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
                        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "campaigns.json"),
                        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "queue.json"))

    assert window.wavespeed_queue_add_button.text() == "Agregar a la cola"


def test_campaign_panel_exposes_queue_then_sequential_start_actions(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    window = MainWindow(CampaignRepository(db), SogniClient(""), ApiKeyStore(tmp_path / "key.txt"))

    assert window.campaign_queue_add_button.text() == "Agregar jobs a campaña seleccionada"
    assert window.start_button.text() == "Iniciar cola / Reanudar"


def test_reference_lists_show_one_based_position_badges(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    window = MainWindow(CampaignRepository(db), SogniClient(""), ApiKeyStore(tmp_path / "key.txt"))
    for name in ("one.png", "two.png"):
        item = QListWidgetItem(name)
        item.setData(Qt.UserRole, str(tmp_path / name))
        window.flux_images.addItem(item)

    window._refresh_reference_positions(window.flux_images)

    assert window.flux_images.itemWidget(window.flux_images.item(0)).findChild(QLabel, "positionBadge").text() == "#1"
    assert window.flux_images.itemWidget(window.flux_images.item(1)).findChild(QLabel, "positionBadge").text() == "#2"


def test_add_jobs_to_selected_campaign_keeps_campaign_count_and_updates_visible_sogni_queue(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)

    original_frames = tmp_path / "original_frames"
    original_frames.mkdir()
    (original_frames / "original.png").write_bytes(b"original")
    original_prompts = tmp_path / "original_prompts.json"
    original_prompts.write_text('[{"id":"P01","name":"Original","text":"Original prompt"}]', encoding="utf-8")
    campaign = CampaignManager(repo).create_campaign(
        name="Selected campaign",
        frames_folder=original_frames,
        prompts_source=original_prompts,
        output_folder=tmp_path / "out",
        model=ModelDescriptor(id="wan22", name="WAN 2.2", media_type="video", parameters={}),
        settings={},
    )

    extra_frames = tmp_path / "extra_frames"
    extra_frames.mkdir()
    (extra_frames / "extra.png").write_bytes(b"extra")
    extra_prompts = tmp_path / "extra_prompts.json"
    extra_prompts.write_text('[{"id":"P02","name":"Extra","text":"Added prompt"}]', encoding="utf-8")

    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"))
    window.current_campaign_id = campaign.id
    window.frames_edit.setText(str(extra_frames))
    window.prompts_edit.setText(str(extra_prompts))
    before_count = len(repo.list_campaigns())

    window._add_jobs_to_selected_campaign()

    assert len(repo.list_campaigns()) == before_count
    assert window.sogni_queue_table.rowCount() == 2
    assert window.sogni_queue_table.item(0, 4).text() == "En cola"
    assert window.sogni_queue_table.item(1, 1).text() == "extra.png"
    assert window.sogni_queue_table.item(1, 2).text() == "Extra"
    assert window.campaign_queue_add_button.text() == "Agregar jobs a campaña seleccionada"


def test_new_campaign_starts_with_empty_queue_until_jobs_are_added(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "one.png").write_bytes(b"one")
    (frames / "two.png").write_bytes(b"two")
    prompts = tmp_path / "prompts.json"
    prompts.write_text('[{"id":"P01","name":"One","text":"Prompt"}]', encoding="utf-8")
    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"))
    window.name_edit.setText("China")
    window.frames_edit.setText(str(frames))
    window.prompts_edit.setText(str(prompts))
    window.output_edit.setText(str(tmp_path / "out"))

    window._create_campaign()

    campaign_id = window.current_campaign_id
    assert repo.get_campaign(campaign_id).name == "China"
    assert repo.count_jobs(campaign_id) == 0
    assert window.sogni_queue_table.rowCount() == 0

    window._add_jobs_to_selected_campaign()

    assert repo.count_jobs(campaign_id) == 2
    assert {job.campaign_id for job in repo.list_jobs(campaign_id)} == {campaign_id}
    assert window.sogni_queue_table.rowCount() == 2


def test_sogni_queue_delete_removes_only_confirmed_pending_job(tmp_path: Path, monkeypatch):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "one.png").write_bytes(b"one")
    (frames / "two.png").write_bytes(b"two")
    prompts = tmp_path / "prompts.json"
    prompts.write_text('[{"id":"P01","name":"One","text":"Prompt"}]', encoding="utf-8")
    campaign = CampaignManager(repo).create_campaign(
        name="China", frames_folder=frames, prompts_source=prompts,
        output_folder=tmp_path / "out",
        model=ModelDescriptor(id="wan22", name="WAN 2.2", media_type="video", parameters={}),
        settings={},
    )
    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"))
    assert window.current_campaign_id == campaign.id
    jobs = repo.list_jobs(campaign.id)
    delete_button = window.sogni_queue_table.cellWidget(0, 6)
    assert delete_button.text() == "Eliminar"
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)

    delete_button.click()

    assert [job.id for job in repo.list_jobs(campaign.id)] == [jobs[1].id]
    assert window.sogni_queue_table.rowCount() == 1


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
    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"),
                        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
                        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "campaigns.json"),
                        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "queue.json"))

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

    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"),
                        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
                        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "campaigns.json"),
                        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "queue.json"))
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
