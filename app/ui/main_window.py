from __future__ import annotations

import base64
import json
import threading
from pathlib import Path

from app.core.campaign_manager import CampaignManager
from app.core.model_settings import ASPECT_RATIOS, build_campaign_settings
from app.core.queue_manager import QueueManager
from app.core.job_runner import JobRunner
from app.database.repositories import CampaignRepository
from app.sogni.schemas import ModelDescriptor
from app.sogni.auth import ApiKeyStore
from app.utils.paths import data_dir
from app.wavespeed.client import WaveSpeedClient
from app.wavespeed.history import WaveSpeedHistoryStore
from app.wavespeed.validation import (
    ASPECT_RATIOS as WAVESPEED_ASPECT_RATIOS,
    RESOLUTIONS as WAVESPEED_RESOLUTIONS,
    build_reference_video_payload,
    validate_local_reference_file,
)

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QComboBox,
        QCheckBox,
        QFileDialog,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QProgressBar,
        QScrollArea,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    Qt = None
    QPixmap = None
    QMainWindow = object


class MainWindow(QMainWindow):
    def __init__(self, repo: CampaignRepository, sogni_client, key_store, wavespeed_client=None, wavespeed_key_store=None) -> None:
        super().__init__()
        self.repo = repo
        self.sogni = sogni_client
        self.key_store = key_store
        self.wavespeed_key_store = wavespeed_key_store or ApiKeyStore(data_dir() / "wavespeed_api_key.txt")
        self.wavespeed = wavespeed_client or WaveSpeedClient(self.wavespeed_key_store.get())
        self.wavespeed_history = WaveSpeedHistoryStore()
        self._wavespeed_task_id: str | None = None
        self._wavespeed_cancel_event = threading.Event()
        self._wavespeed_uploaded: dict[str, dict] = {}
        self.models: list[ModelDescriptor] = []
        self.current_campaign_id: int | None = None
        self._queue_running = False
        self.setWindowTitle("Sogni Video Automator")
        self._build_ui()
        self._load_campaigns()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)

        top = QHBoxLayout()
        self.campaign_combo = QComboBox()
        self.campaign_combo.currentIndexChanged.connect(self._select_campaign_from_combo)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self._load_campaigns)
        top.addWidget(QLabel("Campaign"))
        top.addWidget(self.campaign_combo, 1)
        top.addWidget(refresh_button)
        layout.addLayout(top)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._campaign_tab(), "Campaign")
        self.tabs.addTab(self._frames_tab(), "Frames")
        self.tabs.addTab(self._prompts_tab(), "Prompts")
        self.tabs.addTab(self._jobs_tab(), "Jobs")
        self.tabs.addTab(self._settings_tab(), "Settings")
        self.tabs.addTab(self._wavespeed_tab(), "WaveSpeed")
        layout.addWidget(self.tabs)
        self.setCentralWidget(root)

    def _campaign_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.name_edit = QLineEdit("New Sogni Campaign")
        self.frames_edit = QLineEdit()
        self.prompts_edit = QLineEdit()
        self.output_edit = QLineEdit()
        self.template_edit = QLineEdit("{outfit}__{prompt_id}_{prompt_name}.mp4")
        self.org_combo = QComboBox()
        self.org_combo.addItems(["by_outfit", "flat"])
        self.model_combo = QComboBox()
        self.duration_mode_combo = QComboBox()
        self.duration_mode_combo.addItem("Detectar automaticamente", "auto")
        self.duration_mode_combo.addItem("Manual", "manual")
        self.duration_seconds_edit = QLineEdit("8")
        self.aspect_ratio_combo = QComboBox()
        self.aspect_ratio_combo.addItems(ASPECT_RATIOS)
        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 3)
        self.concurrency_spin.setValue(1)
        self.skip_prompt_processing_check = QCheckBox("Preserve exact H3 prompt (disable Sogni processing)")
        self.skip_prompt_processing_check.setChecked(True)

        form.addRow("Name", self.name_edit)
        form.addRow("Frames folder", self._path_picker(self.frames_edit, True))
        form.addRow("Prompts file", self._path_picker(self.prompts_edit, False))
        form.addRow("Output folder", self._path_picker(self.output_edit, True))
        form.addRow("Model", self.model_combo)
        form.addRow("Duracion", self.duration_mode_combo)
        form.addRow("Segundos", self.duration_seconds_edit)
        form.addRow("Formato", self.aspect_ratio_combo)
        form.addRow("Filename template", self.template_edit)
        form.addRow("Organization", self.org_combo)
        form.addRow("Concurrency", self.concurrency_spin)
        form.addRow("Prompt processing", self.skip_prompt_processing_check)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        fetch_models = QPushButton("Fetch Models")
        fetch_models.clicked.connect(self._fetch_models)
        create = QPushButton("Create Campaign")
        create.clicked.connect(self._create_campaign)
        self.start_button = QPushButton("Start / Resume")
        self.start_button.clicked.connect(self._start_campaign)
        pause = QPushButton("Soft Pause")
        pause.clicked.connect(self._pause_campaign)
        buttons.addWidget(fetch_models)
        buttons.addWidget(create)
        buttons.addWidget(self.start_button)
        buttons.addWidget(pause)
        layout.addLayout(buttons)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.summary_label = QLabel("No campaign selected.")
        layout.addWidget(self.summary_label)
        return page

    def _frames_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        self.frames_table = QTableWidget(0, 3)
        self.frames_table.setHorizontalHeaderLabels(["#", "Frame", "Outfit Name"])
        self.frames_table.itemChanged.connect(self._frame_changed)
        self.frames_table.itemSelectionChanged.connect(self._frame_selected)
        layout.addWidget(self.frames_table, 2)

        preview_panel = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_panel)
        self.frame_preview_label = QLabel("Select a frame")
        self.frame_preview_label.setAlignment(Qt.AlignCenter)
        self.frame_preview_label.setMinimumSize(260, 360)
        self.frame_preview_label.setStyleSheet("border: 1px solid #b8b8b8; background: #151515; color: #d8d8d8;")
        self.frame_preview_meta = QLabel("")
        self.frame_preview_meta.setAlignment(Qt.AlignCenter)
        self.frame_preview_meta.setWordWrap(True)
        preview_layout.addWidget(self.frame_preview_label, 1)
        preview_layout.addWidget(self.frame_preview_meta)
        layout.addWidget(preview_panel, 1)
        return page

    def _prompts_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.prompts_table = QTableWidget(0, 3)
        self.prompts_table.setHorizontalHeaderLabels(["ID", "Prompt Name", "Prompt Text"])
        self.prompts_table.itemChanged.connect(self._prompt_changed)
        layout.addWidget(self.prompts_table)
        return page

    def _jobs_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.jobs_table = QTableWidget(0, 7)
        self.jobs_table.setHorizontalHeaderLabels(["#", "Outfit", "Prompt", "Model", "Status", "Attempts", "Action"])
        self.jobs_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.jobs_table)
        return page

    def _wavespeed_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        layout = QVBoxLayout(inner)

        connection = QGroupBox("WaveSpeedAI · conexión")
        connection_form = QGridLayout(connection)
        self.wavespeed_api_key_edit = QLineEdit(self.wavespeed_key_store.get())
        self.wavespeed_api_key_edit.setEchoMode(QLineEdit.Password)
        save_key = QPushButton("Guardar clave")
        save_key.clicked.connect(self._wavespeed_save_key)
        test_key = QPushButton("Probar conexión")
        test_key.clicked.connect(self._wavespeed_test_connection)
        self.wavespeed_connection_label = QLabel("No probado")
        self.wavespeed_balance_label = QLabel("Saldo: no consultado")
        connection_form.addWidget(QLabel("API key"), 0, 0)
        connection_form.addWidget(self.wavespeed_api_key_edit, 0, 1)
        connection_form.addWidget(save_key, 0, 2)
        connection_form.addWidget(test_key, 1, 0)
        connection_form.addWidget(self.wavespeed_connection_label, 1, 1)
        connection_form.addWidget(self.wavespeed_balance_label, 1, 2)
        endpoint = QLabel(f"Modelo: {self.wavespeed.MODEL_ID}\nEndpoint: {self.wavespeed.BASE_URL}/{self.wavespeed.MODEL_ID}")
        endpoint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        endpoint.setWordWrap(True)
        connection_form.addWidget(endpoint, 2, 0, 1, 3)
        layout.addWidget(connection)

        prompt_group = QGroupBox("Prompt")
        prompt_layout = QVBoxLayout(prompt_group)
        self.wavespeed_prompt_edit = QTextEdit()
        self.wavespeed_prompt_edit.setPlaceholderText("Describe la escena, movimiento, cámara, iluminación y cómo debe usarse cada referencia.")
        self.wavespeed_prompt_edit.setMinimumHeight(100)
        prompt_layout.addWidget(self.wavespeed_prompt_edit)
        layout.addWidget(prompt_group)

        references = QGroupBox("Referencias multimodales")
        references_layout = QGridLayout(references)
        self.wavespeed_reference_lists: dict[str, QListWidget] = {}
        reference_specs = (
            ("reference_images", "Imágenes (0/10)", "Images (*.png *.jpg *.jpeg *.webp *.gif)"),
            ("reference_videos", "Videos (0/5)", "Videos (*.mp4 *.mov)"),
            ("reference_audios", "Audios (0/5)", "Audio (*.mp3 *.wav *.m4a *.aac *.ogg)"),
        )
        for column, (kind, label, file_filter) in enumerate(reference_specs):
            box = QWidget()
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            list_widget = QListWidget()
            list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
            list_widget.setMinimumHeight(95)
            self.wavespeed_reference_lists[kind] = list_widget
            box_layout.addWidget(QLabel(label))
            box_layout.addWidget(list_widget)
            buttons = QHBoxLayout()
            add = QPushButton("Agregar")
            add.clicked.connect(lambda _checked=False, k=kind, f=file_filter: self._wavespeed_add_files(k, f))
            remove = QPushButton("Quitar")
            remove.clicked.connect(lambda _checked=False, k=kind: self._wavespeed_remove_files(k))
            buttons.addWidget(add)
            buttons.addWidget(remove)
            box_layout.addLayout(buttons)
            references_layout.addWidget(box, 0, column)
        layout.addWidget(references)

        parameters = QGroupBox("Wan 3.0 Reference-to-Video · parámetros")
        parameters_form = QGridLayout(parameters)
        self.wavespeed_resolution_combo = QComboBox()
        self.wavespeed_resolution_combo.addItems(list(WAVESPEED_RESOLUTIONS))
        self.wavespeed_resolution_combo.setCurrentText("720p")
        self.wavespeed_aspect_combo = QComboBox()
        self.wavespeed_aspect_combo.addItems(list(WAVESPEED_ASPECT_RATIOS))
        self.wavespeed_aspect_combo.setCurrentText("16:9")
        self.wavespeed_duration_spin = QSpinBox()
        self.wavespeed_duration_spin.setRange(2, 30)
        self.wavespeed_duration_spin.setValue(5)
        self.wavespeed_prompt_expansion_check = QCheckBox("enable_prompt_expansion")
        self.wavespeed_audio_check = QCheckBox("enable_audio")
        self.wavespeed_audio_check.setChecked(True)
        self.wavespeed_random_seed_check = QCheckBox("Seed aleatorio (-1)")
        self.wavespeed_random_seed_check.setChecked(True)
        self.wavespeed_seed_spin = QSpinBox()
        self.wavespeed_seed_spin.setRange(0, 2_147_483_647)
        self.wavespeed_seed_spin.setEnabled(False)
        self.wavespeed_random_seed_check.toggled.connect(self.wavespeed_seed_spin.setDisabled)
        parameters_form.addWidget(QLabel("Resolución"), 0, 0)
        parameters_form.addWidget(self.wavespeed_resolution_combo, 0, 1)
        parameters_form.addWidget(QLabel("Aspect ratio"), 0, 2)
        parameters_form.addWidget(self.wavespeed_aspect_combo, 0, 3)
        parameters_form.addWidget(QLabel("Duración (s)"), 1, 0)
        parameters_form.addWidget(self.wavespeed_duration_spin, 1, 1)
        parameters_form.addWidget(self.wavespeed_prompt_expansion_check, 1, 2)
        parameters_form.addWidget(self.wavespeed_audio_check, 1, 3)
        parameters_form.addWidget(self.wavespeed_random_seed_check, 2, 0, 1, 2)
        parameters_form.addWidget(self.wavespeed_seed_spin, 2, 2)
        layout.addWidget(parameters)

        advanced = QGroupBox("Opciones API avanzadas")
        advanced_form = QGridLayout(advanced)
        self.wavespeed_sync_check = QCheckBox("enable_sync_mode")
        self.wavespeed_base64_check = QCheckBox("enable_base64_output")
        self.wavespeed_webhook_edit = QLineEdit()
        self.wavespeed_webhook_edit.setPlaceholderText("https://tu-servidor-publico.example/webhooks/wavespeed")
        self.wavespeed_webhook_edit.textChanged.connect(self._wavespeed_update_advanced_controls)
        advanced_form.addWidget(self.wavespeed_sync_check, 0, 0)
        advanced_form.addWidget(self.wavespeed_base64_check, 0, 1)
        advanced_form.addWidget(QLabel("Webhook HTTPS externo"), 1, 0)
        advanced_form.addWidget(self.wavespeed_webhook_edit, 1, 1, 1, 2)
        advanced_form.addWidget(QLabel("Sync y Base64 no son compatibles con webhook. El Safety Checker solo está documentado en Playground."), 2, 0, 1, 3)
        layout.addWidget(advanced)

        output = QGroupBox("Estimación, salida y ejecución")
        output_form = QGridLayout(output)
        self.wavespeed_output_edit = QLineEdit(str(data_dir() / "wavespeed_outputs"))
        choose_output = QPushButton("Carpeta")
        choose_output.clicked.connect(self._wavespeed_choose_output)
        self.wavespeed_price_label = QLabel("Precio: no estimado")
        estimate = QPushButton("Estimar precio")
        estimate.clicked.connect(self._wavespeed_estimate_price)
        self.wavespeed_generate_button = QPushButton("Generar video")
        self.wavespeed_generate_button.clicked.connect(self._wavespeed_generate)
        cancel_polling = QPushButton("Detener polling")
        cancel_polling.clicked.connect(self._wavespeed_cancel_event.set)
        self.wavespeed_task_edit = QLineEdit()
        self.wavespeed_task_edit.setPlaceholderText("Task ID para consultar/eliminar")
        query = QPushButton("Consultar")
        query.clicked.connect(self._wavespeed_query_task)
        delete = QPushButton("Eliminar tarea")
        delete.clicked.connect(self._wavespeed_delete_task)
        output_form.addWidget(QLabel("Output"), 0, 0)
        output_form.addWidget(self.wavespeed_output_edit, 0, 1)
        output_form.addWidget(choose_output, 0, 2)
        output_form.addWidget(estimate, 1, 0)
        output_form.addWidget(self.wavespeed_price_label, 1, 1)
        output_form.addWidget(self.wavespeed_generate_button, 1, 2)
        output_form.addWidget(cancel_polling, 2, 2)
        output_form.addWidget(self.wavespeed_task_edit, 3, 0, 1, 2)
        output_form.addWidget(query, 3, 2)
        output_form.addWidget(delete, 4, 2)
        layout.addWidget(output)

        self.wavespeed_status_label = QLabel("Listo. Agregá al menos una referencia.")
        self.wavespeed_progress = QProgressBar()
        self.wavespeed_progress.setRange(0, 0)
        self.wavespeed_progress.setVisible(False)
        self.wavespeed_raw_edit = QTextEdit()
        self.wavespeed_raw_edit.setReadOnly(True)
        self.wavespeed_raw_edit.setMaximumHeight(150)
        layout.addWidget(self.wavespeed_status_label)
        layout.addWidget(self.wavespeed_progress)
        layout.addWidget(self.wavespeed_raw_edit)

        self.wavespeed_history_table = QTableWidget(0, 5)
        self.wavespeed_history_table.setHorizontalHeaderLabels(["Task", "Estado", "Archivo", "Creado", "Error"])
        self.wavespeed_history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(QLabel("Historial local WaveSpeed"))
        layout.addWidget(self.wavespeed_history_table)

        scroll.setWidget(inner)
        outer.addWidget(scroll)
        self._wavespeed_refresh_history()
        self._wavespeed_update_advanced_controls()
        return page

    def _wavespeed_add_files(self, kind: str, file_filter: str) -> None:
        selected, _ = QFileDialog.getOpenFileNames(self, "Seleccionar referencias", filter=file_filter)
        limits = {"reference_images": 10, "reference_videos": 5, "reference_audios": 5}
        widget = self.wavespeed_reference_lists[kind]
        existing = {widget.item(index).data(Qt.UserRole) for index in range(widget.count())}
        for selected_path in selected:
            path = str(Path(selected_path).resolve())
            if path in existing or widget.count() >= limits[kind]:
                continue
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            widget.addItem(item)
            existing.add(path)
        self.wavespeed_status_label.setText(f"{widget.count()}/{limits[kind]} referencias en {kind}.")

    def _wavespeed_remove_files(self, kind: str) -> None:
        widget = self.wavespeed_reference_lists[kind]
        for item in widget.selectedItems():
            widget.takeItem(widget.row(item))

    def _wavespeed_choose_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de salida")
        if selected:
            self.wavespeed_output_edit.setText(selected)

    def _wavespeed_save_key(self) -> None:
        key = self.wavespeed_api_key_edit.text().strip()
        self.wavespeed_key_store.save(key)
        self.wavespeed.api_key = key
        self.wavespeed_connection_label.setText("Clave guardada localmente")

    def _wavespeed_update_advanced_controls(self) -> None:
        has_webhook = bool(self.wavespeed_webhook_edit.text().strip())
        if has_webhook:
            self.wavespeed_sync_check.setChecked(False)
            self.wavespeed_base64_check.setChecked(False)
        self.wavespeed_sync_check.setEnabled(not has_webhook)
        self.wavespeed_base64_check.setEnabled(not has_webhook)

    def _wavespeed_snapshot(self) -> dict:
        def paths(kind: str) -> list[str]:
            widget = self.wavespeed_reference_lists[kind]
            return [str(widget.item(index).data(Qt.UserRole)) for index in range(widget.count())]

        return {
            "prompt": self.wavespeed_prompt_edit.toPlainText(),
            "reference_images": paths("reference_images"),
            "reference_videos": paths("reference_videos"),
            "reference_audios": paths("reference_audios"),
            "resolution": self.wavespeed_resolution_combo.currentText(),
            "aspect_ratio": self.wavespeed_aspect_combo.currentText(),
            "duration": self.wavespeed_duration_spin.value(),
            "enable_prompt_expansion": self.wavespeed_prompt_expansion_check.isChecked(),
            "enable_audio": self.wavespeed_audio_check.isChecked(),
            "seed": -1 if self.wavespeed_random_seed_check.isChecked() else self.wavespeed_seed_spin.value(),
            "enable_sync_mode": self.wavespeed_sync_check.isChecked(),
            "enable_base64_output": self.wavespeed_base64_check.isChecked(),
            "webhook_url": self.wavespeed_webhook_edit.text().strip(),
            "output_dir": self.wavespeed_output_edit.text().strip(),
        }

    def _wavespeed_upload_and_build(self, snapshot: dict) -> tuple[dict, dict[str, dict]]:
        uploaded: dict[str, dict] = {}
        for kind in ("reference_images", "reference_videos", "reference_audios"):
            urls = []
            for file_name in snapshot[kind]:
                cache_key = str(Path(file_name).resolve())
                validate_local_reference_file(Path(cache_key), kind)
                cached = self._wavespeed_uploaded.get(cache_key)
                if cached:
                    media = cached
                else:
                    self._post_wavespeed_event("status", f"Subiendo {Path(file_name).name}...")
                    media = self.wavespeed.upload_file(Path(file_name))
                    self._wavespeed_uploaded[cache_key] = media
                uploaded[cache_key] = media
                urls.append(media["download_url"])
            snapshot[kind] = urls
        payload = build_reference_video_payload(
            prompt=snapshot["prompt"],
            reference_images=snapshot["reference_images"],
            reference_videos=snapshot["reference_videos"],
            reference_audios=snapshot["reference_audios"],
            resolution=snapshot["resolution"],
            aspect_ratio=snapshot["aspect_ratio"],
            duration=snapshot["duration"],
            enable_prompt_expansion=snapshot["enable_prompt_expansion"],
            enable_audio=snapshot["enable_audio"],
            seed=snapshot["seed"],
            enable_sync_mode=snapshot["enable_sync_mode"],
            enable_base64_output=snapshot["enable_base64_output"],
        )
        return payload, uploaded

    def _wavespeed_generate(self) -> None:
        snapshot = self._wavespeed_snapshot()
        self._wavespeed_start_busy("Preparando generación...")
        self._wavespeed_cancel_event.clear()

        def task():
            payload, _uploaded = self._wavespeed_upload_and_build(snapshot)
            prediction = self.wavespeed.submit(payload, webhook_url=snapshot["webhook_url"] or None)
            self._post_wavespeed_event("status", f"Task {prediction.id} creado ({prediction.status}).")
            if prediction.status not in {"completed", "failed", "cancelled", "timeout", "deleted"} and not snapshot["webhook_url"]:
                prediction = self.wavespeed.poll_result(
                    prediction.id,
                    on_update=lambda item: self._post_wavespeed_event("status", f"Task {item.id}: {item.status}"),
                    cancel_event=self._wavespeed_cancel_event,
                )
            output_file = ""
            if prediction.status == "completed" and prediction.outputs:
                output_file = self._wavespeed_save_output(prediction.outputs[0], snapshot, prediction.id)
            self.wavespeed_history.add({
                "task_id": prediction.id,
                "status": prediction.status,
                "output_file": output_file,
                "error": prediction.error,
                "payload": payload,
                "created_at": prediction.created_at,
            })
            return {"prediction": prediction, "output_file": output_file, "payload": payload}

        self._run_wavespeed_worker(task, "generate")

    def _wavespeed_save_output(self, output: object, snapshot: dict, task_id: str) -> str:
        output_dir = Path(snapshot["output_dir"] or data_dir() / "wavespeed_outputs")
        destination = output_dir / f"wavespeed_{task_id}.mp4"
        if isinstance(output, str) and output.startswith(("http://", "https://")):
            return str(self.wavespeed.download_output(output, destination))
        if isinstance(output, str):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(base64.b64decode(output, validate=True))
            return str(destination)
        raise ValueError("WaveSpeed returned an unsupported output value.")

    def _wavespeed_start_busy(self, message: str) -> None:
        self.wavespeed_generate_button.setEnabled(False)
        self.wavespeed_progress.setVisible(True)
        self.wavespeed_status_label.setText(message)

    def _wavespeed_finish(self, result: dict | Exception) -> None:
        self.wavespeed_generate_button.setEnabled(True)
        self.wavespeed_progress.setVisible(False)
        if isinstance(result, Exception):
            self.wavespeed_status_label.setText(f"Error: {result}")
            return
        prediction = result["prediction"]
        self._wavespeed_task_id = prediction.id
        self.wavespeed_task_edit.setText(prediction.id)
        self.wavespeed_raw_edit.setPlainText(json.dumps(prediction.raw, ensure_ascii=False, indent=2))
        message = f"{prediction.id}: {prediction.status}"
        if result.get("output_file"):
            message += f" · guardado en {result['output_file']}"
        self.wavespeed_status_label.setText(message)
        self._wavespeed_refresh_history()

    def _wavespeed_test_connection(self) -> None:
        self._wavespeed_save_key()
        self.wavespeed_connection_label.setText("Consultando...")

        def task():
            balance = self.wavespeed.get_balance()
            return balance

        self._run_wavespeed_worker(task, "balance")

    def _wavespeed_estimate_price(self) -> None:
        snapshot = self._wavespeed_snapshot()
        self._wavespeed_start_busy("Subiendo referencias para estimar precio...")

        def task():
            payload, _uploaded = self._wavespeed_upload_and_build(snapshot)
            return self.wavespeed.estimate_price(payload)

        self._run_wavespeed_worker(task, "price")

    def _wavespeed_query_task(self) -> None:
        task_id = self.wavespeed_task_edit.text().strip() or self._wavespeed_task_id
        if not task_id:
            self.wavespeed_status_label.setText("Ingresá un Task ID.")
            return

        def task():
            return self.wavespeed.get_result(task_id)

        self._run_wavespeed_worker(task, "query")

    def _wavespeed_delete_task(self) -> None:
        task_id = self.wavespeed_task_edit.text().strip() or self._wavespeed_task_id
        if not task_id:
            self.wavespeed_status_label.setText("Ingresá un Task ID.")
            return
        if not QMessageBox.question(self, "Eliminar tarea", f"¿Eliminar definitivamente {task_id}?") == QMessageBox.Yes:
            return

        def task():
            return self.wavespeed.delete_tasks([task_id])

        self._run_wavespeed_worker(task, "delete")

    def _wavespeed_refresh_history(self) -> None:
        if not hasattr(self, "wavespeed_history_table"):
            return
        rows = self.wavespeed_history.list()
        self.wavespeed_history_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            values = [
                item.get("task_id", ""),
                item.get("status", ""),
                item.get("output_file", ""),
                item.get("created_at", ""),
                item.get("error", ""),
            ]
            for column, value in enumerate(values):
                self.wavespeed_history_table.setItem(row, column, QTableWidgetItem(str(value)))

    def _run_wavespeed_worker(self, task, kind: str) -> None:
        def run():
            try:
                result = task()
                self._post_wavespeed_event(kind, result)
            except Exception as exc:
                self._post_wavespeed_event(kind, exc)

        threading.Thread(target=run, daemon=True).start()

    def _post_wavespeed_event(self, kind: str, payload) -> None:
        app = QApplication.instance()
        if app is not None:
            app.postEvent(self, _WaveSpeedEvent(kind, payload))

    def _settings_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("Sogni API")
        form = QGridLayout(group)
        self.api_key_edit = QLineEdit(self.key_store.get())
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        save = QPushButton("Save Key")
        save.clicked.connect(self._save_key)
        test = QPushButton("Test Connection")
        test.clicked.connect(self._test_connection)
        self.connection_label = QLabel("Not tested")
        form.addWidget(QLabel("API Key"), 0, 0)
        form.addWidget(self.api_key_edit, 0, 1)
        form.addWidget(save, 0, 2)
        form.addWidget(test, 1, 1)
        form.addWidget(self.connection_label, 1, 2)
        layout.addWidget(group)
        layout.addStretch()
        return page

    def _path_picker(self, edit: QLineEdit, directory: bool) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("Browse")
        button.clicked.connect(lambda: self._browse(edit, directory))
        layout.addWidget(edit)
        layout.addWidget(button)
        return widget

    def _browse(self, edit: QLineEdit, directory: bool) -> None:
        if directory:
            selected = QFileDialog.getExistingDirectory(self, "Select folder")
        else:
            selected, _ = QFileDialog.getOpenFileName(self, "Select prompts", filter="Prompts (*.json *.csv *.txt)")
        if selected:
            edit.setText(selected)

    def _fetch_models(self) -> None:
        try:
            self.models = self.sogni.fetch_video_models()
            self.model_combo.clear()
            for model in self.models:
                self.model_combo.addItem(model.name, model)
        except Exception as exc:
            QMessageBox.warning(self, "Model catalog", str(exc))

    def _create_campaign(self) -> None:
        try:
            model = self.model_combo.currentData()
            if model is None:
                model = ModelDescriptor(id=self.model_combo.currentText().strip() or "ltx25", name=self.model_combo.currentText().strip() or "LTX 2.5", media_type="video")
            campaign = CampaignManager(self.repo).create_campaign(
                name=self.name_edit.text(),
                frames_folder=Path(self.frames_edit.text()),
                prompts_source=Path(self.prompts_edit.text()),
                output_folder=Path(self.output_edit.text()),
                model=model,
                settings=build_campaign_settings(
                    self.duration_mode_combo.currentData(),
                    self.duration_seconds_edit.text(),
                    self.aspect_ratio_combo.currentText(),
                    skip_prompt_processing=self.skip_prompt_processing_check.isChecked(),
                ),
                filename_template=self.template_edit.text(),
                organization_mode=self.org_combo.currentText(),
                concurrency=self.concurrency_spin.value(),
            )
            self.current_campaign_id = campaign.id
            self._load_campaigns()
            self._refresh_tables()
        except Exception as exc:
            QMessageBox.critical(self, "Create campaign", str(exc))

    def _load_campaigns(self) -> None:
        self.campaign_combo.blockSignals(True)
        self.campaign_combo.clear()
        for campaign in self.repo.list_campaigns():
            self.campaign_combo.addItem(f"#{campaign.id} {campaign.name} [{campaign.status}]", campaign.id)
        self.campaign_combo.blockSignals(False)
        if self.campaign_combo.count():
            self._select_campaign_from_combo()

    def _select_campaign_from_combo(self) -> None:
        self.current_campaign_id = self.campaign_combo.currentData()
        self._refresh_tables()

    def _refresh_tables(self) -> None:
        if not self.current_campaign_id:
            return
        campaign = self.repo.get_campaign(self.current_campaign_id)
        frames = self.repo.list_frames(campaign.id)
        prompts = self.repo.list_prompts(campaign.id)
        jobs = self.repo.list_jobs(campaign.id)
        frame_by_id = {frame.id: frame for frame in frames}
        prompt_by_id = {prompt.id: prompt for prompt in prompts}
        counts = self.repo.job_counts(campaign.id)
        done = counts.get("DONE", 0)
        total = len(jobs)
        self.progress.setValue(int(done * 100 / total) if total else 0)
        self.summary_label.setText(f"{done} / {total} done | Failed: {counts.get('FAILED', 0)} | Pending: {counts.get('PENDING', 0)}")

        self.frames_table.blockSignals(True)
        self.frames_table.setRowCount(len(frames))
        for row, frame in enumerate(frames):
            self._set_item(self.frames_table, row, 0, str(row + 1), frame.id, editable=False)
            self._set_item(self.frames_table, row, 1, frame.filename, frame.id, editable=False)
            self._set_item(self.frames_table, row, 2, frame.outfit_name, frame.id)
        self.frames_table.blockSignals(False)
        if frames:
            self.frames_table.selectRow(0)
            self._frame_selected()

        self.prompts_table.blockSignals(True)
        self.prompts_table.setRowCount(len(prompts))
        for row, prompt in enumerate(prompts):
            self._set_item(self.prompts_table, row, 0, prompt.prompt_code, prompt.id, editable=False)
            self._set_item(self.prompts_table, row, 1, prompt.prompt_name, prompt.id)
            self._set_item(self.prompts_table, row, 2, prompt.prompt_text, prompt.id)
        self.prompts_table.blockSignals(False)

        self.jobs_table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            frame = frame_by_id[job.frame_id]
            prompt = prompt_by_id[job.prompt_id]
            for col, text in enumerate([
                str(job.order_index),
                frame.outfit_name,
                prompt.prompt_name,
                campaign.model_name,
                _job_status_label(job.status),
                str(job.attempt_count),
            ]):
                self._set_item(self.jobs_table, row, col, text, job.id, editable=False)
            if job.status == "FAILED":
                retry = QPushButton("Reintentar")
                retry.clicked.connect(lambda checked=False, job_id=job.id: self._retry_job(job_id))
                self.jobs_table.setCellWidget(row, 6, retry)

    def _set_item(self, table: QTableWidget, row: int, col: int, text: str, record_id: int, editable: bool = True) -> None:
        item = QTableWidgetItem(text)
        item.setData(Qt.UserRole, record_id)
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        table.setItem(row, col, item)

    def _frame_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 2:
            self.repo.update_frame_outfit(int(item.data(Qt.UserRole)), item.text())
            self._refresh_tables()

    def _frame_selected(self) -> None:
        selected = self.frames_table.selectedItems()
        if not selected or not self.current_campaign_id:
            self.frame_preview_label.setText("Select a frame")
            self.frame_preview_label.setPixmap(QPixmap())
            self.frame_preview_meta.setText("")
            return
        frame_id = int(selected[0].data(Qt.UserRole))
        frame = next((item for item in self.repo.list_frames(self.current_campaign_id) if item.id == frame_id), None)
        if frame is None:
            return
        image_path = Path(frame.file_path)
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            self.frame_preview_label.setPixmap(QPixmap())
            self.frame_preview_label.setText("Preview unavailable")
        else:
            scaled = pixmap.scaled(320, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.frame_preview_label.setText("")
            self.frame_preview_label.setPixmap(scaled)
        self.frame_preview_meta.setText(f"{frame.filename}\n{frame.outfit_name}")

    def _prompt_changed(self, item: QTableWidgetItem) -> None:
        if item.column() in {1, 2}:
            row = item.row()
            name_item = self.prompts_table.item(row, 1)
            text_item = self.prompts_table.item(row, 2)
            self.repo.update_prompt(int(item.data(Qt.UserRole)), name_item.text(), text_item.text())
            self._refresh_tables()

    def _save_key(self) -> None:
        self.key_store.save(self.api_key_edit.text())
        self.sogni.api_key = self.api_key_edit.text().strip()
        self.connection_label.setText("Saved")

    def _test_connection(self) -> None:
        ok, message = self.sogni.test_connection()
        self.connection_label.setText(message)
        if ok:
            self._fetch_models()

    def _start_campaign(self) -> None:
        if not self.current_campaign_id or self._queue_running:
            return
        self._queue_running = True
        self.start_button.setEnabled(False)
        manager = QueueManager(self.repo, JobRunner(self.repo, self.sogni))
        thread = threading.Thread(target=lambda: self._run_queue(manager), daemon=True)
        thread.start()

    def _retry_job(self, job_id: int) -> None:
        if not self.current_campaign_id or self._queue_running:
            return
        self.repo.retry_job(job_id)
        self._refresh_tables()
        self._start_campaign()

    def _run_queue(self, manager: QueueManager) -> None:
        try:
            manager.resume(self.current_campaign_id)
        finally:
            QApplication.instance().postEvent(self, _RefreshEvent())

    def _pause_campaign(self) -> None:
        if self.current_campaign_id:
            self.repo.update_campaign_status(self.current_campaign_id, "PAUSE_REQUESTED")
            self._refresh_tables()

    def customEvent(self, event):
        if event.type() == _RefreshEvent.TYPE:
            self._queue_running = False
            self.start_button.setEnabled(True)
            self._load_campaigns()
        elif event.type() == _WaveSpeedEvent.TYPE:
            self._handle_wavespeed_event(event.kind, event.payload)

    def _handle_wavespeed_event(self, kind: str, payload) -> None:
        if kind == "status":
            self.wavespeed_status_label.setText(str(payload))
        elif kind == "generate":
            self._wavespeed_finish(payload)
        elif kind == "balance":
            if isinstance(payload, Exception):
                self.wavespeed_connection_label.setText(f"Error: {payload}")
            else:
                self.wavespeed_connection_label.setText("Conectado")
                self.wavespeed_balance_label.setText(f"Saldo: ${float(payload):.4f}")
        elif kind == "price":
            self.wavespeed_generate_button.setEnabled(True)
            self.wavespeed_progress.setVisible(False)
            if isinstance(payload, Exception):
                self.wavespeed_price_label.setText(f"Precio: error: {payload}")
            else:
                amount = payload.get("discounted_price", payload.get("price", "?"))
                self.wavespeed_price_label.setText(f"Precio estimado: ${amount} USD")
        elif kind == "query":
            if isinstance(payload, Exception):
                self.wavespeed_status_label.setText(f"Error: {payload}")
            else:
                self._wavespeed_task_id = payload.id
                self.wavespeed_raw_edit.setPlainText(json.dumps(payload.raw, ensure_ascii=False, indent=2))
                self.wavespeed_status_label.setText(f"{payload.id}: {payload.status}")
        elif kind == "delete":
            if isinstance(payload, Exception):
                self.wavespeed_status_label.setText(f"Error: {payload}")
            else:
                self.wavespeed_status_label.setText(f"Tareas eliminadas: {payload}")


if Qt is not None:
    from PySide6.QtCore import QEvent

    class _RefreshEvent(QEvent):
        TYPE = QEvent.Type(QEvent.registerEventType())

        def __init__(self) -> None:
            super().__init__(self.TYPE)

    class _WaveSpeedEvent(QEvent):
        TYPE = QEvent.Type(QEvent.registerEventType())

        def __init__(self, kind: str, payload) -> None:
            super().__init__(self.TYPE)
            self.kind = kind
            self.payload = payload


def _job_status_label(status: str) -> str:
    return {
        "PENDING": "En cola",
        "RETRY_WAIT": "Reintento pendiente",
        "PREPARING": "Preparando",
        "UPLOADING_FRAME": "Subiendo imagen",
        "SUBMITTING": "Enviando a Sogni",
        "QUEUED": "En cola remota",
        "GENERATING": "Generando",
        "COMPLETED_REMOTE": "Completado remoto",
        "DOWNLOADING": "Descargando",
        "DONE": "Completado",
        "FAILED": "Fallo",
    }.get(status, status)
