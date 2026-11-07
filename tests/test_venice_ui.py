"""Offscreen tests for the standalone Venice editor."""

from __future__ import annotations

import base64
import threading
import time
from pathlib import Path

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QLineEdit

from app.sogni.auth import ApiKeyStore
from app.ui import venice as venice_ui
from app.ui import venice_batch as venice_batch_ui
from app.ui.venice import VeniceEditWidget
from app.venice.client import VeniceApiError, VeniceImageResult
from app.venice.history import VeniceHistoryStore
from app.venice.validation import ModelProfile


QWEN = "qwen-image-2-1-pro-edit"
FLUX = "flux-2-max-edit"


def _app():
    return QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout=3):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        _app().processEvents()
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "timed out waiting for worker signal"


def _image(path: Path) -> Path:
    assert QImage(256, 256, QImage.Format_RGB32).save(str(path), "PNG")
    return path


def _profile(model_id=QWEN, *, resolutions=("1K", "2K"), ratios=("auto", "1:1")):
    return ModelProfile(model_id, "Qwen Image 2.1 Pro" if model_id == QWEN else "FLUX 2 Max",
                        3, 1000, resolutions, ratios, resolutions[0] if resolutions else "")


class FakeVeniceClient:
    def __init__(self, profiles=None, *, result=None, error=None, block=None):
        self.profiles = profiles if profiles is not None else {QWEN: _profile()}
        self.result = result
        self.error = error
        self.block = block
        self.api_key = ""
        self.calls = []
        self.started = threading.Event()

    def list_edit_models(self):
        return self.profiles

    def multi_edit(self, payload):
        self.calls.append(payload)
        self.started.set()
        if self.block is not None:
            assert self.block.wait(3)
        if self.error is not None:
            raise self.error
        return self.result


def _widget(tmp_path, client):
    _app()
    widget = VeniceEditWidget(client, ApiKeyStore(tmp_path / "key.txt"),
                              VeniceHistoryStore(tmp_path / "history.json"))
    _wait_until(lambda: not widget._catalog_active)
    return widget


def test_main_window_registers_venice_with_separate_default_key_store(tmp_path, monkeypatch):
    from app.database.db import Database
    from app.database.repositories import CampaignRepository
    from app.sogni.client import SogniClient
    from app.ui import main_window as main_window_ui

    _app()
    monkeypatch.setattr(main_window_ui, "data_dir", lambda: tmp_path)
    db = Database(tmp_path / "app.db")
    db.initialize()
    client = FakeVeniceClient()
    window = main_window_ui.MainWindow(
        CampaignRepository(db), SogniClient(""), ApiKeyStore(tmp_path / "sogni_api_key.txt"),
        wavespeed_key_store=ApiKeyStore(tmp_path / "wavespeed_api_key.txt"),
        byteplus_key_store=ApiKeyStore(tmp_path / "byteplus_api_key.txt"),
        venice_client=client,
    )
    try:
        labels = [window.tabs.tabText(index) for index in range(window.tabs.count())]
        assert {"WaveSpeed", "BytePlus", "Imágenes", "Venice"} <= set(labels)
        assert window.tabs.widget(labels.index("Venice")) is window.venice_tab
        assert isinstance(window.venice_tab, VeniceEditWidget)
        assert window.venice_key_store.path == tmp_path / "venice_api_key.txt"
        assert window.venice_key_store.env_name == "VENICE_API_KEY"
        assert window.venice_tab.key_store.path == window.venice_key_store.path
        assert window.venice_key_store.path not in {
            window.key_store.path, window.wavespeed_key_store.path, window.byteplus_key_store.path,
        }
        assert isinstance(window.venice_tab.history_store, VeniceHistoryStore)
    finally:
        window.close()


def test_venice_slots_preserve_visible_order_after_reorder(tmp_path):
    widget = _widget(tmp_path, FakeVeniceClient())
    scene, face, body = (_image(tmp_path / f"{name}.png") for name in ("scene", "face", "body"))
    widget.set_reference_path(0, scene)
    widget.set_reference_path(1, face)
    widget.set_reference_path(2, body)
    widget.move_reference(2, -1)
    widget.prompt_edit.setPlainText("  literal prompt\n")
    assert widget._ordered_reference_paths() == [scene, body, face]
    assert [widget.single_page.layout().itemAt(i).layout().itemAt(1).widget().text() for i in (4, 5, 6)] == [
        "scene.png", "body.png", "face.png"]
    snapshot = widget._build_request_snapshot()
    payload = widget._build_payload_from_snapshot(snapshot)
    assert payload["images"] == [base64.b64encode(path.read_bytes()).decode("ascii")
                                 for path in (scene, body, face)]
    assert payload["prompt"] == "  literal prompt\n"
    widget.close()


def test_snapshot_is_not_changed_by_later_control_edits(tmp_path):
    widget = _widget(tmp_path, FakeVeniceClient())
    scene = _image(tmp_path / "scene.png")
    face = _image(tmp_path / "face.png")
    widget.set_reference_path(0, scene)
    widget.set_reference_path(1, face)
    widget.prompt_edit.setPlainText("original")
    snapshot = widget._build_request_snapshot()
    widget.move_reference(1, -1)
    widget.prompt_edit.setPlainText("changed")
    widget.output_format_combo.setCurrentText("webp")
    assert snapshot["reference_paths"] == [scene, face]
    assert snapshot["options"]["output_format"] == "png"
    assert widget._build_payload_from_snapshot(snapshot)["prompt"] == "original"
    widget.close()


def test_catalog_offers_only_available_models_and_updates_options(tmp_path):
    client = FakeVeniceClient({FLUX: _profile(FLUX, resolutions=(), ratios=("auto",)),
                               QWEN: _profile()})
    widget = _widget(tmp_path, client)
    assert widget.model_combo.currentData() == QWEN
    assert [widget.model_combo.itemData(i) for i in range(widget.model_combo.count())] == [QWEN, FLUX]
    assert widget.resolution_combo.isEnabled()
    assert [widget.resolution_combo.itemText(i) for i in range(widget.resolution_combo.count())] == ["1K", "2K"]
    widget.model_combo.setCurrentIndex(widget.model_combo.findData(FLUX))
    assert not widget.resolution_combo.isEnabled()
    assert [widget.aspect_combo.itemText(i) for i in range(widget.aspect_combo.count())] == ["auto"]
    widget.close()


def test_catalog_failure_does_not_select_qwen_or_offer_unavailable_models(tmp_path):
    client = FakeVeniceClient({FLUX: _profile(FLUX, resolutions=())})
    widget = _widget(tmp_path, client)
    assert widget.model_combo.currentData() == FLUX
    assert widget.model_combo.findData(QWEN) == -1
    widget.close()


def test_empty_catalog_keeps_generation_disabled(tmp_path):
    widget = _widget(tmp_path, FakeVeniceClient({}))
    assert widget.model_combo.count() == 0
    assert not widget.generate_button.isEnabled()
    widget.close()


def test_key_field_uses_venice_environment_and_password_echo(tmp_path, monkeypatch):
    monkeypatch.setenv("VENICE_API_KEY", "venice-only")
    monkeypatch.setenv("SOGNI_API_KEY", "sogni-only")
    client = FakeVeniceClient()
    widget = _widget(tmp_path, client)
    assert widget.key_edit.echoMode() == QLineEdit.Password
    assert widget.key_edit.text() == "venice-only"
    assert client.api_key == "venice-only"
    assert not widget.save_key_button.isEnabled()
    assert "VENICE_API_KEY" in widget.key_source_label.text()
    widget.key_edit.setText("new-file-key")
    widget._save_key()
    assert client.api_key == "venice-only"
    assert not (tmp_path / "venice_api_key.txt").exists()
    widget.close()


def test_shared_sogni_key_store_is_not_mutated_or_written(tmp_path, monkeypatch):
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.delenv("SOGNI_API_KEY", raising=False)
    sogni_path = tmp_path / "sogni_api_key.txt"
    sogni_path.write_text("sogni-only", encoding="utf-8")
    shared_store = ApiKeyStore(sogni_path)
    client = FakeVeniceClient()
    _app()
    widget = VeniceEditWidget(client, shared_store, VeniceHistoryStore(tmp_path / "history.json"))
    _wait_until(lambda: not widget._catalog_active)
    assert shared_store.env_name == "SOGNI_API_KEY"
    assert shared_store.path == sogni_path
    assert widget.key_edit.text() != "sogni-only"
    widget.key_edit.setText("venice-only")
    widget.save_key_button.click()
    assert sogni_path.read_text(encoding="utf-8") == "sogni-only"
    assert (tmp_path / "venice_api_key.txt").read_text(encoding="utf-8") == "venice-only"
    assert client.api_key == "venice-only"
    widget.close()


def test_duplicate_click_starts_only_one_request(tmp_path):
    release = threading.Event()
    client = FakeVeniceClient(block=release, result=VeniceImageResult(b"bad", "image/png", "req", {}))
    widget = _widget(tmp_path, client)
    widget.set_reference_path(0, _image(tmp_path / "scene.png"))
    widget.prompt_edit.setPlainText("edit")
    try:
        widget.generate_button.click()
        _wait_until(client.started.is_set)
        widget.generate_button.click()
        assert len(client.calls) == 1
        assert not widget.generate_button.isEnabled()
    finally:
        release.set()
        _wait_until(lambda: not widget._request_active)
        widget.close()


def test_binary_response_saved_previewed_and_history_sanitized(tmp_path):
    output = _image(tmp_path / "sample.png").read_bytes()
    client = FakeVeniceClient(result=VeniceImageResult(output, "image/png", "req-1", {}))
    widget = _widget(tmp_path, client)
    scene = _image(tmp_path / "scene.png")
    face = _image(tmp_path / "face.png")
    widget.set_reference_path(0, scene)
    widget.set_reference_path(1, face)
    widget.prompt_edit.setPlainText("secret prompt")
    widget.output_dir = tmp_path / "outputs"
    widget.generate_button.click()
    _wait_until(lambda: not widget._request_active)
    files = list(widget.output_dir.glob("*.png"))
    assert len(files) == 1 and files[0].read_bytes() == output
    assert not widget.preview_label.pixmap().isNull()
    assert widget.history_table.rowCount() == 1
    assert widget.history_table.item(0, 1).text() == QWEN
    assert widget.history_table.item(0, 2).text() == "secret prompt"
    assert widget.history_table.item(0, 3).text() == "scene.png, face.png"
    assert widget.history_table.item(0, 4).text() == str(files[0])
    disk = (tmp_path / "history.json").read_text(encoding="utf-8")
    assert '"slot": 1' in disk and '"slot": 2' in disk
    assert base64.b64encode(output).decode("ascii") not in disk
    assert '"prompt": "secret prompt"' in disk
    assert f'"output_file": "{files[0].name}"' in disk
    assert str(files[0]) not in disk
    widget.close()


def test_save_failure_retains_paid_response_for_local_save_without_second_request(tmp_path, monkeypatch):
    output = _image(tmp_path / "sample.png").read_bytes()
    client = FakeVeniceClient(result=VeniceImageResult(output, "image/png", "req-1", {}))
    widget = _widget(tmp_path, client)
    scene = _image(tmp_path / "scene.png")
    widget.set_reference_path(0, scene)
    widget.prompt_edit.setPlainText("original prompt")
    original_save = venice_ui.save_venice_output
    attempts = []

    def fail_once(result, directory):
        attempts.append(Path(directory))
        if len(attempts) == 1:
            raise OSError("disk full")
        return original_save(result, directory)

    monkeypatch.setattr(venice_ui, "save_venice_output", fail_once)
    widget.output_dir = tmp_path / "unwritable"
    widget.generate_button.click()
    _wait_until(lambda: not widget._request_active)
    assert len(client.calls) == 1
    assert "save" in widget.status_label.text().lower()
    assert "no new" in widget.status_label.text().lower()
    assert widget.history_table.rowCount() == 0
    widget.prompt_edit.setPlainText("changed prompt")
    widget.set_reference_path(0, None)
    widget.output_dir = tmp_path / "writable"
    widget.generate_button.click()
    _wait_until(lambda: not widget._request_active)
    assert len(client.calls) == 1
    assert attempts == [tmp_path / "unwritable", tmp_path / "writable"]
    assert list(widget.output_dir.glob("*.png"))[0].read_bytes() == output
    assert widget.history_table.item(0, 2).text() == "original prompt"
    assert widget.history_table.item(0, 3).text() == "scene.png"
    widget.close()


def test_received_image_can_be_saved_after_catalog_becomes_empty(tmp_path, monkeypatch):
    release = threading.Event()
    output = _image(tmp_path / "sample.png").read_bytes()
    client = FakeVeniceClient(block=release,
                              result=VeniceImageResult(output, "image/png", "req-1", {}))
    widget = _widget(tmp_path, client)
    widget.set_reference_path(0, _image(tmp_path / "scene.png"))
    widget.prompt_edit.setPlainText("original prompt")
    original_save = venice_ui.save_venice_output
    attempts = []

    def fail_once(result, directory):
        attempts.append(Path(directory))
        if len(attempts) == 1:
            raise OSError("local save failed")
        return original_save(result, directory)

    monkeypatch.setattr(venice_ui, "save_venice_output", fail_once)
    widget.generate_button.click()
    try:
        _wait_until(client.started.is_set)
        client.profiles = {}
        widget.refresh_button.click()
        _wait_until(lambda: not widget._catalog_active)
        assert widget.model_combo.count() == 0
    finally:
        release.set()
    _wait_until(lambda: not widget._request_active)
    assert widget.generate_button.text() == "Save received image"
    assert widget.generate_button.isEnabled()
    widget.refresh_button.click()
    _wait_until(lambda: not widget._catalog_active)
    assert widget.generate_button.isEnabled()
    widget.output_dir = tmp_path / "writable"
    widget.generate_button.click()
    _wait_until(lambda: not widget._request_active)
    assert len(client.calls) == 1
    assert list(widget.output_dir.glob("*.png"))[0].read_bytes() == output
    assert widget.history_table.item(0, 2).text() == "original prompt"
    assert not widget.generate_button.isEnabled()
    widget.close()


def test_saved_non_previewable_image_reports_preview_failure(tmp_path):
    client = FakeVeniceClient(result=VeniceImageResult(b"not-a-png", "image/png", "req", {}))
    widget = _widget(tmp_path, client)
    widget.set_reference_path(0, _image(tmp_path / "scene.png"))
    widget.prompt_edit.setPlainText("edit")
    widget.output_dir = tmp_path / "outputs"
    widget.generate_button.click()
    _wait_until(lambda: not widget._request_active)
    assert list(widget.output_dir.glob("*.png"))[0].read_bytes() == b"not-a-png"
    assert "preview" in widget.status_label.text().lower()
    assert "unavailable" in widget.status_label.text().lower()
    assert widget.preview_label.text() == "Preview unavailable"
    widget.close()


def test_provider_error_preserves_controls_and_shows_sanitized_message(tmp_path):
    client = FakeVeniceClient(error=VeniceApiError("Venice API returned HTTP 429."))
    widget = _widget(tmp_path, client)
    scene = _image(tmp_path / "scene.png")
    widget.set_reference_path(0, scene)
    widget.prompt_edit.setPlainText("keep this prompt")
    widget.generate_button.click()
    _wait_until(lambda: not widget._request_active)
    assert widget.prompt_edit.toPlainText() == "keep this prompt"
    assert widget._ordered_reference_paths() == [scene]
    assert widget.status_label.text() == "Venice API returned HTTP 429."
    widget.close()


def test_venice_has_editable_batch_page_and_one_click_snapshot(tmp_path, monkeypatch):
    client = FakeVeniceClient()
    widget = _widget(tmp_path, client)
    batch = widget.batch_tab
    assert [widget.pages.tabText(i) for i in range(widget.pages.count())] == ["Single image", "Batch"]
    assert batch.master_prompt_edit.isReadOnly() is False

    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    output_parent = tmp_path / "outputs"
    face = _image(tmp_path / "face.png")
    body = _image(tmp_path / "body.png")
    source = _image(source_dir / "photo.jpg")
    batch.source_dir_edit.setText(str(source_dir))
    batch.output_parent_edit.setText(str(output_parent))
    batch.face_path_edit.setText(str(face))
    batch.body_path_edit.setText(str(body))
    batch.master_prompt_edit.setPlainText("snapshot master")
    captured = []

    def fake_run(client_arg, profile, config, progress=None, stop_event=None):
        captured.append(config)
        return output_parent / "run"

    monkeypatch.setattr(venice_batch_ui, "run_batch", fake_run)
    batch.process_button.click()
    _wait_until(lambda: bool(captured))
    batch.source_dir_edit.setText(str(tmp_path / "changed-source"))
    batch.master_prompt_edit.setPlainText("changed master")
    assert captured[0].input_dir == source_dir
    assert captured[0].master_prompt == "snapshot master"
    assert captured[0].face_path == face
    assert captured[0].body_path == body
    assert captured[0].input_dir != Path(batch.source_dir_edit.text())
    assert source.exists()
    widget.close()


def test_batch_missing_master_or_references_fails_before_worker(tmp_path, monkeypatch):
    client = FakeVeniceClient()
    widget = _widget(tmp_path, client)
    batch = widget.batch_tab
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    batch.source_dir_edit.setText(str(source_dir))
    batch.output_parent_edit.setText(str(tmp_path / "outputs"))
    batch.master_prompt_edit.clear()
    started = []
    monkeypatch.setattr(venice_batch_ui, "run_batch", lambda *args, **kwargs: started.append(True))
    batch.process_button.click()
    assert started == []
    assert "master" in batch.status_label.text().lower()
    widget.close()


def test_batch_status_event_exposes_saved_prompt_for_optional_review(tmp_path):
    client = FakeVeniceClient()
    widget = _widget(tmp_path, client)
    batch = widget.batch_tab
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("saved prompt", encoding="utf-8")
    batch._on_progress(venice_batch_ui.BatchEvent(
        index=1, filename="photo.jpg", stage="prompt", status="saved",
        prompt_path=prompt_path,
    ))
    batch.progress_table.selectRow(0)
    _app().processEvents()
    assert batch.prompt_preview.toPlainText() == "saved prompt"
    widget.close()


def test_batch_close_requests_stop_for_active_worker(tmp_path, monkeypatch):
    client = FakeVeniceClient()
    widget = _widget(tmp_path, client)
    batch = widget.batch_tab
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    batch.source_dir_edit.setText(str(source_dir))
    batch.output_parent_edit.setText(str(tmp_path / "outputs"))
    batch.face_path_edit.setText(str(_image(tmp_path / "face.png")))
    batch.body_path_edit.setText(str(_image(tmp_path / "body.png")))
    batch.master_prompt_edit.setPlainText("master")
    started = threading.Event()
    release = threading.Event()
    observed = []

    def fake_run(client_arg, profile, config, progress=None, stop_event=None):
        observed.append(stop_event)
        started.set()
        release.wait(3)
        return tmp_path / "run"

    monkeypatch.setattr(venice_batch_ui, "run_batch", fake_run)
    batch.process_button.click()
    _wait_until(started.is_set)
    batch.close()
    assert observed[0].is_set()
    assert batch._closed
    release.set()
    _wait_until(lambda: not batch._active)
    widget.close()


def test_batch_disables_venice_key_changes_while_active(tmp_path, monkeypatch):
    client = FakeVeniceClient()
    widget = _widget(tmp_path, client)
    batch = widget.batch_tab
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    batch.source_dir_edit.setText(str(source_dir))
    batch.output_parent_edit.setText(str(tmp_path / "outputs"))
    batch.face_path_edit.setText(str(_image(tmp_path / "face.png")))
    batch.body_path_edit.setText(str(_image(tmp_path / "body.png")))
    batch.master_prompt_edit.setPlainText("master")
    started = threading.Event()
    release = threading.Event()

    def fake_run(client_arg, profile, config, progress=None, stop_event=None):
        started.set()
        release.wait(3)
        return tmp_path / "run"

    monkeypatch.setattr(venice_batch_ui, "run_batch", fake_run)
    batch.process_button.click()
    _wait_until(started.is_set)
    assert widget.key_edit.isReadOnly()
    assert not widget.save_key_button.isEnabled()
    release.set()
    _wait_until(lambda: not batch._active)
    assert not widget.key_edit.isReadOnly()
    widget.close()


def test_batch_preflight_error_does_not_expose_reference_path(tmp_path):
    client = FakeVeniceClient()
    widget = _widget(tmp_path, client)
    batch = widget.batch_tab
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _image(source_dir / "photo.jpg")
    bad_face = tmp_path / "private-face.png"
    bad_face.write_text("not an image", encoding="utf-8")
    batch.source_dir_edit.setText(str(source_dir))
    batch.output_parent_edit.setText(str(tmp_path / "outputs"))
    batch.face_path_edit.setText(str(bad_face))
    batch.body_path_edit.setText(str(_image(tmp_path / "body.png")))
    batch.master_prompt_edit.setPlainText("master")
    batch.process_button.click()
    _wait_until(lambda: not batch._active)
    assert str(tmp_path) not in batch.status_label.text()
    assert "image" in batch.status_label.text().lower()
    widget.close()
