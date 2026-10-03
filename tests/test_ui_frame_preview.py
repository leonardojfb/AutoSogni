from pathlib import Path
import json
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
from app.wavespeed.validation import (
    SEEDANCE_MODEL_ID,
    SEEDANCE_I2V_SPICY_MODEL_ID,
    FLUX_MODEL_ID,
    FACE_SWAP_MODEL_ID,
)
from app.wavespeed.flux_queue import FluxCampaignStore
from app.wavespeed.history import WaveSpeedHistoryStore
from app.wavespeed.schemas import WaveSpeedPrediction


def _app():
    return QApplication.instance() or QApplication([])


def test_use_campaign_as_base_loads_selected_editable_copy(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    source_id = repo.insert_campaign({
        "name": "R2V original", "status": "COMPLETED",
        "model_id": "minimax-h3-ref2va-fp8_r2v", "model_name": "MiniMax H3 R2V",
        "frames_folder": "C:/frames", "prompts_source": "C:/prompts.json",
        "output_folder": "C:/out", "filename_template": "{prompt_id}.mp4",
        "organization_mode": "flat", "concurrency": 2,
        "settings_json": json.dumps({
            "duration_mode": "manual", "duration": 12, "aspectRatio": "16:9",
            "safe_content_filter": False, "skipPromptProcessing": False,
            "loras": ["style-a", "style-b"], "loraStrengths": [0.7, 0.4],
            "reference_media": ["C:/refs/first.png", "C:/refs/second.mp4"],
        }),
    })
    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"),
                        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
                        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "campaigns.json"),
                        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "queue.json"))
    window.model_combo.addItem("MiniMax H3 R2V", ModelDescriptor(
        id="minimax-h3-ref2va-fp8_r2v", name="MiniMax H3 R2V", media_type="video",
    ))
    window.current_campaign_id = source_id

    window._use_campaign_as_base()

    clone_id = window.current_campaign_id
    assert window.use_campaign_as_base_button.text() == "Usar como base"
    assert clone_id != source_id
    assert window.campaign_combo.currentData() == clone_id
    assert repo.count_jobs(clone_id) == 0
    assert repo.list_frames(clone_id) == []
    assert repo.list_prompts(clone_id) == []
    assert window.name_edit.text() == "R2V original (copia)"
    assert window.frames_edit.text() == "C:/frames"
    assert window.prompts_edit.text() == "C:/prompts.json"
    assert window.output_edit.text() == "C:/out"
    assert window.template_edit.text() == "{prompt_id}.mp4"
    assert window.org_combo.currentText() == "flat"
    assert window.concurrency_spin.value() == 2
    assert window.model_combo.currentData().id == "minimax-h3-ref2va-fp8_r2v"
    assert window.duration_mode_combo.currentData() == "manual"
    assert window.duration_seconds_edit.text() == "12"
    assert window.aspect_ratio_combo.currentText() == "16:9"
    assert not window.sogni_sensitive_filter_check.isChecked()
    assert not window.skip_prompt_processing_check.isChecked()
    assert window._selected_sogni_loras == [["style-a", 0.7], ["style-b", 0.4]]
    assert [window.sogni_reference_list.item(index).data(Qt.UserRole)
            for index in range(window.sogni_reference_list.count())] == [
        "C:/refs/first.png", "C:/refs/second.mp4",
    ]


def test_edited_campaign_base_is_saved_before_adding_jobs(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    old_reference = tmp_path / "old.png"
    old_reference.write_bytes(b"old")
    new_reference = tmp_path / "new.mp4"
    new_reference.write_bytes(b"new")
    prompts = tmp_path / "prompts.json"
    prompts.write_text('[{"id":"P01","name":"One","text":"Prompt"}]', encoding="utf-8")
    edited_prompts = tmp_path / "edited-prompts.json"
    edited_prompts.write_text('[{"id":"P02","name":"Two","text":"Edited prompt"}]', encoding="utf-8")
    source_id = repo.insert_campaign({
        "name": "Source", "status": "COMPLETED",
        "model_id": "minimax-h3-fl2va-fp8_r2v_balanced", "model_name": "MiniMax H3 R2V",
        "frames_folder": str(tmp_path), "prompts_source": str(prompts),
        "output_folder": str(tmp_path / "out"), "filename_template": "old.mp4",
        "organization_mode": "flat", "concurrency": 1,
        "settings_json": json.dumps({
            "duration_mode": "manual", "duration": 8, "aspectRatio": "9:16",
            "loras": ["old-style"], "loraStrengths": [0.5],
            "reference_media": [str(old_reference)],
        }),
    })
    source = repo.get_campaign(source_id)
    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"),
                        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
                        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "campaigns.json"),
                        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "queue.json"))
    window.current_campaign_id = source_id
    window._use_campaign_as_base()
    clone_id = window.current_campaign_id
    window.model_combo.addItem("MiniMax H3 R2V alternate", ModelDescriptor(
        id="minimax-h3-ref2va-fp8_r2v", name="MiniMax H3 R2V alternate", media_type="video",
    ))
    window.model_combo.setCurrentIndex(window.model_combo.count() - 1)
    window.name_edit.setText("Edited base")
    window.frames_edit.setText(str(tmp_path / "edited-frames"))
    window.prompts_edit.setText(str(edited_prompts))
    window.output_edit.setText(str(tmp_path / "edited-out"))
    window.template_edit.setText("{prompt_id}.mp4")
    window.org_combo.setCurrentText("by_outfit")
    window.concurrency_spin.setValue(2)
    window.duration_seconds_edit.setText("12")
    window.aspect_ratio_combo.setCurrentText("16:9")
    window.sogni_sensitive_filter_check.setChecked(False)
    window.skip_prompt_processing_check.setChecked(False)
    window._selected_sogni_loras = [["new-style-a", 0.7], ["new-style-b", 0.4]]
    window.sogni_reference_list.clear()
    reference_item = QListWidgetItem(new_reference.name)
    reference_item.setData(Qt.UserRole, str(new_reference))
    window.sogni_reference_list.addItem(reference_item)

    window._add_jobs_to_selected_campaign()

    clone = repo.get_campaign(clone_id)
    settings = json.loads(clone.settings_json)
    assert window.current_campaign_id == clone_id
    assert clone.name == "Edited base"
    assert clone.model_id == "minimax-h3-ref2va-fp8_r2v"
    assert clone.model_name == "MiniMax H3 R2V alternate"
    assert clone.frames_folder == str(tmp_path / "edited-frames")
    assert clone.prompts_source == str(edited_prompts)
    assert clone.output_folder == str(tmp_path / "edited-out")
    assert clone.filename_template == "{prompt_id}.mp4"
    assert clone.organization_mode == "by_outfit"
    assert clone.concurrency == 2
    assert settings["duration"] == 12
    assert settings["aspectRatio"] == "16:9"
    assert settings["safe_content_filter"] is False
    assert settings["skipPromptProcessing"] is False
    assert settings["loras"] == ["new-style-a", "new-style-b"]
    assert settings["loraStrengths"] == [0.7, 0.4]
    assert settings["reference_media"] == [str(new_reference)]
    assert repo.count_jobs(clone_id) == 1
    assert json.loads(repo.list_jobs(clone_id)[0].reference_media_json) == [str(new_reference)]
    assert repo.get_campaign(source_id) == source
    assert repo.count_jobs(source_id) == 0


def test_switching_away_from_base_stops_stale_form_persistence(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "one.png").write_bytes(b"frame")
    prompts = tmp_path / "prompts.json"
    prompts.write_text('[{"id":"P01","name":"One","text":"Prompt"}]', encoding="utf-8")
    source_id = repo.insert_campaign({
        "name": "Source", "status": "COMPLETED", "model_id": "wan22", "model_name": "WAN 2.2",
        "frames_folder": str(frames), "prompts_source": str(prompts),
        "output_folder": str(tmp_path / "out"), "filename_template": "source.mp4",
        "organization_mode": "flat", "concurrency": 1,
        "settings_json": json.dumps({"duration_mode": "manual", "duration": 8, "aspectRatio": "9:16"}),
    })
    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"),
                        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
                        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "campaigns.json"),
                        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "queue.json"))
    window.current_campaign_id = source_id
    window._use_campaign_as_base()
    clone_id = window.current_campaign_id
    original_clone = repo.get_campaign(clone_id)

    window.campaign_combo.setCurrentIndex(window.campaign_combo.findData(source_id))
    assert window._editable_base_campaign_id is None
    window.campaign_combo.setCurrentIndex(window.campaign_combo.findData(clone_id))
    window.name_edit.setText("Stale form name")
    window.duration_seconds_edit.setText("12")
    window._add_jobs_to_selected_campaign()

    assert repo.get_campaign(clone_id) == original_clone
    assert repo.count_jobs(clone_id) == 1
    assert repo.count_jobs(source_id) == 0


def test_reselecting_r2v_base_loads_its_inputs_before_adding_jobs(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    base_reference = tmp_path / "base.png"
    base_reference.write_bytes(b"base")
    other_reference = tmp_path / "other.mp4"
    other_reference.write_bytes(b"other")
    base_prompts = tmp_path / "base-prompts.json"
    base_prompts.write_text('[{"id":"B01","name":"Base","text":"Base prompt"}]', encoding="utf-8")
    other_prompts = tmp_path / "other-prompts.json"
    other_prompts.write_text('[{"id":"O01","name":"Other","text":"Other prompt"}]', encoding="utf-8")

    def insert_campaign(name: str, reference: Path, prompts: Path) -> int:
        return repo.insert_campaign({
            "name": name, "status": "READY",
            "model_id": "minimax-h3-fl2va-fp8_r2v_balanced", "model_name": "MiniMax H3 R2V",
            "frames_folder": str(reference.parent / name), "prompts_source": str(prompts),
            "output_folder": str(tmp_path / f"{name}-out"), "filename_template": "{prompt_id}.mp4",
            "organization_mode": "flat", "concurrency": 1,
            "settings_json": json.dumps({
                "duration_mode": "manual", "duration": 8, "aspectRatio": "9:16",
                "reference_media": [str(reference)],
            }),
        })

    source_id = insert_campaign("base", base_reference, base_prompts)
    other_id = insert_campaign("other", other_reference, other_prompts)
    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"),
                        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
                        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "campaigns.json"),
                        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "queue.json"))
    window.campaign_combo.setCurrentIndex(window.campaign_combo.findData(source_id))
    window._use_campaign_as_base()
    clone_id = window.current_campaign_id
    clone_before = repo.get_campaign(clone_id)

    window.campaign_combo.setCurrentIndex(window.campaign_combo.findData(other_id))
    assert window.prompts_edit.text() == str(other_prompts)
    assert window._selected_sogni_reference_paths() == [other_reference]
    window.campaign_combo.setCurrentIndex(window.campaign_combo.findData(clone_id))
    assert window.frames_edit.text() == clone_before.frames_folder
    assert window.prompts_edit.text() == str(base_prompts)
    assert window.output_edit.text() == clone_before.output_folder
    assert window._selected_sogni_reference_paths() == [base_reference]
    assert window._editable_base_campaign_id is None

    window._add_jobs_to_selected_campaign()

    assert repo.get_campaign(clone_id) == clone_before
    assert repo.count_jobs(clone_id) == 1
    job = repo.list_jobs(clone_id)[0]
    assert json.loads(job.reference_media_json) == [str(base_reference)]
    assert repo.list_frames(clone_id)[0].file_path == str(base_reference)
    assert repo.list_prompts(clone_id)[0].prompt_code == "B01"
    assert repo.count_jobs(other_id) == 0
    assert repo.count_jobs(source_id) == 0


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


def test_completed_campaign_job_exposes_retry_button(tmp_path: Path):
    _app()
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    campaign_id = repo.insert_campaign({
        "name": "Retry Campaign", "status": "READY", "model_id": "wan22", "model_name": "WAN 2.2",
        "frames_folder": str(tmp_path), "prompts_source": "", "output_folder": str(tmp_path / "out"),
        "filename_template": "{outfit}.mp4", "organization_mode": "flat", "concurrency": 1,
        "settings_json": "{}",
    })
    frame_id = repo.insert_frame(campaign_id, "frame.png", "frame.png", "Blue Test", "sha")
    prompt_id = repo.insert_prompt(campaign_id, "P01", "One", "Prompt")
    job_id = repo.insert_job(campaign_id, frame_id, prompt_id, 1, "job-key")
    repo.set_job_status(job_id, "DONE")
    window = MainWindow(repo, SogniClient(""), ApiKeyStore(tmp_path / "key.txt"),
                        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed-key.txt"),
                        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "campaigns.json"),
                        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "queue.json"))
    window.current_campaign_id = campaign_id

    window._refresh_tables()

    assert window.jobs_table.cellWidget(0, 6).text() == "Reintentar"
    assert window.sogni_queue_table.cellWidget(0, 6).text() == "Reintentar"


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
    window.wavespeed_safety_checker_check.setChecked(False)
    window.wavespeed_resolution_combo.setCurrentText("4k")
    window.wavespeed_aspect_combo.setCurrentText("21:9")
    window.wavespeed_duration_spin.setValue(15)
    payload, _ = window._wavespeed_upload_and_build(window._wavespeed_snapshot())
    assert window.wavespeed_model_combo.currentData() == SEEDANCE_MODEL_ID
    assert payload["prompt"] == "Seedance prompt"
    assert payload["resolution"] == "4k"
    assert payload["safety_checker"] is False
    assert "seed" not in payload and "enable_prompt_expansion" not in payload

    start_image = tmp_path / "start.png"
    end_image = tmp_path / "end.png"
    start_image.write_bytes(b"start-image")
    end_image.write_bytes(b"end-image")
    images = window.wavespeed_reference_lists["reference_images"]
    for image_path in (start_image, end_image):
        item = QListWidgetItem(image_path.name)
        item.setData(Qt.UserRole, str(image_path))
        images.addItem(item)
    window.wavespeed_model_combo.setCurrentIndex(2)
    window.wavespeed_prompt_edit.setPlainText("Move the camera forward")
    window.wavespeed_resolution_combo.setCurrentText("4k")
    window.wavespeed_aspect_combo.setCurrentText("21:9")
    window.wavespeed_duration_spin.setValue(15)
    window.wavespeed_audio_check.setChecked(False)
    window.wavespeed_random_seed_check.setChecked(False)
    window.wavespeed_seed_spin.setValue(123)
    assert not window.wavespeed_safety_checker_check.isHidden()
    window.wavespeed.upload_file = lambda path: {"download_url": f"https://cdn.test/{Path(path).name}"}

    spicy_payload, _ = window._wavespeed_upload_and_build(window._wavespeed_snapshot())

    assert window.wavespeed_model_combo.currentData() == SEEDANCE_I2V_SPICY_MODEL_ID
    assert spicy_payload == {
        "image": "https://cdn.test/start.png",
        "last_image": "https://cdn.test/end.png",
        "prompt": "Move the camera forward",
        "aspect_ratio": "21:9",
        "resolution": "4k",
        "duration": 15,
        "generate_audio": False,
        "seed": 123,
        "safety_checker": False,
    }
    assert window.wavespeed_reference_boxes["reference_videos"].isHidden()
    assert window.wavespeed_reference_boxes["reference_audios"].isHidden()
    assert not window.wavespeed_reference_boxes["reference_images"].isHidden()
    assert not window.wavespeed_queue_add_button.isEnabled()

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
