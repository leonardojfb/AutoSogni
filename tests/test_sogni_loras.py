import httpx
import pytest

from app.core.model_settings import build_campaign_settings
from app.sogni.client import SogniClient
from app.sogni.loras import validate_lora_selection


MODEL = "minimax-h3-fl2va-fp8_i2v"


def _wait_for_lora_catalog(dialog):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if not dialog.isVisible():
        dialog.show()
    app.processEvents()
    worker = dialog._catalog_worker
    if worker is not None:
        assert worker.wait(5000)
    app.processEvents()


def test_fetch_loras_filters_public_and_personal_by_exact_model():
    def respond(request):
        if request.url.path == "/v1/loras/comfy":
            assert request.url.params["modelId"] == MODEL
            return httpx.Response(200, json={"data": {"loras": [
                {"loraId": "motion", "name": "Motion", "modelIds": [MODEL], "ui": {"min": 0, "max": 2, "default": 0.6, "step": 0.1}},
                {"loraId": "wrong", "name": "Wrong", "modelIds": ["other"], "ui": {}},
            ]}})
        assert request.url.path == "/v1/loras/personal/catalog"
        return httpx.Response(200, json={"data": {"loras": [
            {"loraId": "personal-abc", "name": "Mine", "modelIds": [MODEL], "ui": {"min": 0, "max": 1, "default": 1, "step": 0.05}},
        ]}})

    client = SogniClient("key", client=httpx.Client(transport=httpx.MockTransport(respond), base_url=SogniClient.BASE_URL))
    rows = client.fetch_loras(MODEL, include_personal=True)
    assert [row["loraId"] for row in rows] == ["motion", "personal-abc"]


def test_validate_loras_preserves_order_and_catalog_strength_bounds():
    catalog = [
        {"loraId": "motion", "modelIds": [MODEL], "ui": {"min": 0, "max": 2}},
        {"loraId": "personal-abc", "modelIds": [MODEL], "ui": {"min": 0, "max": 1}},
    ]
    assert validate_lora_selection(MODEL, [["motion", 0.6], ["personal-abc", 0.9]], catalog) == [
        ["motion", 0.6], ["personal-abc", 0.9]
    ]
    with pytest.raises(ValueError, match="strength"):
        validate_lora_selection(MODEL, [["personal-abc", 1.2]], catalog)
    with pytest.raises(ValueError, match="compatible"):
        validate_lora_selection("minimax-h3-fastvideo-int8_i2v_turbo", [["motion", 0.6]], catalog)


def test_campaign_loras_reach_h3_workflow_without_changing_prompt():
    settings = build_campaign_settings("manual", "6", "9:16", loras=[["motion", 0.6]], safe_content_filter=False)
    assert settings["loras"] == ["motion"]
    assert settings["loraStrengths"] == [0.6]
    payload = SogniClient.build_image_to_video_payload(
        "Job", "Exact prompt", MODEL, settings, {"kind": "image", "url": "https://example.com/frame.png"}
    )
    step = payload["input"]["steps"][0]
    assert step["arguments"]["prompt"] == "Exact prompt"
    assert step["arguments"]["loras"] == ["motion"]
    assert step["arguments"]["loraStrengths"] == [0.6]
    assert payload["safe_content_filter"] is False
    assert "safe_content_filter" not in step["arguments"]


def test_fasth3_i2v_loras_use_animate_photo_selector():
    payload = SogniClient.build_image_to_video_payload(
        "Job", "Exact prompt", "minimax-h3-fastvideo-int8_i2v_turbo",
        {"duration": 6, "loras": ["motion"], "loraStrengths": [0.6]},
        {"kind": "image", "url": "https://example.com/frame.png"},
    )
    step = payload["input"]["steps"][0]
    assert step["toolName"] == "animate_photo"
    assert step["arguments"]["videoModel"] == "minimax-h3-fasth3-i2v-turbo"
    assert step["arguments"]["sourceImageIndex"] == -1


def test_personal_import_requires_rights_confirmation_and_uses_api():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(202, json={"data": {"status": "queued"}})

    client = SogniClient("key", client=httpx.Client(transport=httpx.MockTransport(respond), base_url=SogniClient.BASE_URL))
    with pytest.raises(ValueError, match="permission"):
        client.import_personal_lora("https://huggingface.co/a/b/resolve/main/x.safetensors", "Mine", MODEL, False)
    assert requests == []
    result = client.import_personal_lora("https://huggingface.co/a/b/resolve/main/x.safetensors", "Mine", MODEL, True)
    assert result["status"] == "queued"
    assert requests[0].url.path == "/v1/loras/personal"
    assert requests[0].headers["Authorization"] == "Bearer key"


def test_lora_dialog_collects_checked_rows_and_strengths():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from app.ui.sogni_loras import SogniLoraDialog

    app = QApplication.instance() or QApplication([])

    class CatalogClient:
        def fetch_loras(self, model_id, include_personal=False):
            assert model_id == MODEL
            assert include_personal is False
            return [{"loraId": "motion", "name": "Better Motion", "modelIds": [MODEL],
                     "description": "Natural body motion", "ui": {"min": 0, "max": 2, "default": 0.6, "step": 0.1}}]

    dialog = SogniLoraDialog(CatalogClient(), MODEL, [])
    _wait_for_lora_catalog(dialog)
    assert dialog.table.rowCount() == 1
    dialog.table.item(0, 0).setCheckState(Qt.Checked)
    dialog.table.cellWidget(0, 1).setValue(0.8)
    dialog.accept()
    assert dialog.selected_loras == [["motion", 0.8]]


def test_lora_dialog_reorders_stack_and_keeps_selection_on_refresh():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from app.ui.sogni_loras import SogniLoraDialog

    app = QApplication.instance() or QApplication([])

    class CatalogClient:
        def fetch_loras(self, model_id, include_personal=False):
            return [{"loraId": item, "name": item, "modelIds": [MODEL],
                     "ui": {"min": 0, "max": 1, "default": 0.5}}
                    for item in ("first", "second")]

    dialog = SogniLoraDialog(CatalogClient(), MODEL, [])
    _wait_for_lora_catalog(dialog)
    dialog.table.item(0, 0).setCheckState(Qt.Checked)
    dialog.table.item(1, 0).setCheckState(Qt.Checked)
    dialog.table.cellWidget(0, 1).setValue(0.4)
    dialog.table.cellWidget(1, 1).setValue(0.8)
    dialog.table.selectRow(1)
    dialog._move(-1)
    dialog.refresh()
    _wait_for_lora_catalog(dialog)
    dialog.accept()
    assert dialog.selected_loras == [["second", 0.8], ["first", 0.4]]


def test_lora_dialog_shows_one_based_position_for_selected_stack():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from app.ui.sogni_loras import SogniLoraDialog

    app = QApplication.instance() or QApplication([])

    class CatalogClient:
        def fetch_loras(self, model_id, include_personal=False):
            return [{"loraId": item, "name": item, "modelIds": [MODEL], "ui": {}}
                    for item in ("first", "second")]

    dialog = SogniLoraDialog(CatalogClient(), MODEL, [])
    _wait_for_lora_catalog(dialog)
    dialog.table.item(0, 0).setCheckState(Qt.Checked)
    dialog.table.item(1, 0).setCheckState(Qt.Checked)

    assert dialog.table.horizontalHeaderItem(3).text() == "Posición"
    assert dialog.table.item(0, 3).text() == "#1"
    assert dialog.table.item(1, 3).text() == "#2"
    dialog.table.selectRow(1)
    dialog._move(-1)
    assert dialog.table.item(0, 3).text() == "#1"
    assert dialog.table.item(0, 0).data(Qt.UserRole) == "second"


def test_campaign_ui_opens_loras_for_selected_h3_model(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from app.database.db import Database
    from app.database.repositories import CampaignRepository
    from app.sogni.auth import ApiKeyStore
    from app.sogni.schemas import ModelDescriptor
    from app.ui.main_window import MainWindow
    from app.wavespeed.flux_queue import FluxCampaignStore
    from app.wavespeed.queue import WaveSpeedCampaignStore, WaveSpeedQueueStore

    app = QApplication.instance() or QApplication([])
    db = Database(tmp_path / "ui.db")
    db.initialize()
    window = MainWindow(
        CampaignRepository(db), SogniClient(""), ApiKeyStore(tmp_path / "key.txt"),
        wavespeed_campaign_store=WaveSpeedCampaignStore(tmp_path / "wavespeed-campaigns.json"),
        wavespeed_queue_store=WaveSpeedQueueStore(tmp_path / "wavespeed-queue.json"),
        flux_campaign_store=FluxCampaignStore(tmp_path / "flux-campaigns.json"),
    )
    window.model_combo.addItem("H3", ModelDescriptor(MODEL, "H3", "video"))

    class Dialog:
        def __init__(self, client, model_id, selection, parent, *, show_personal):
            assert model_id == MODEL
            assert selection == []
            assert show_personal is False
            self.selected_loras = [["motion", 0.6]]
            self.catalog = [{"loraId": "motion", "modelIds": [MODEL], "ui": {"max": 2}}]

        def exec(self):
            return True

    monkeypatch.setattr("app.ui.main_window.SogniLoraDialog", Dialog)
    assert window.sogni_lora_button.isEnabled()
    window.sogni_lora_button.click()
    assert window._selected_sogni_loras == [["motion", 0.6]]
    assert "motion" in window.sogni_lora_label.text()
    window.close()
