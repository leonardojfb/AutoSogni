from __future__ import annotations

import base64
import json
import threading
from pathlib import Path
from urllib.parse import urlparse

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
from app.wavespeed.flux_queue import FluxCampaign, FluxCampaignStore, FluxQueue, FluxQueueItem, FluxQueueRunner
from app.wavespeed.queue import (
    QueueStatus,
    WaveSpeedCampaign,
    WaveSpeedCampaignStore,
    WaveSpeedQueueItem,
    WaveSpeedQueueStore,
)
from app.wavespeed.queue_runner import WaveSpeedQueueRunner
from app.ui.wavespeed_queue import WaveSpeedQueueWidget
from app.ui.flux_queue import FluxQueueWidget
from app.wavespeed.validation import (
    ASPECT_RATIOS as WAVESPEED_ASPECT_RATIOS,
    RESOLUTIONS as WAVESPEED_RESOLUTIONS,
    MODEL_ID as WAN_MODEL_ID,
    SEEDANCE_MODEL_ID,
    FLUX_MODEL_ID,
    FACE_SWAP_MODEL_ID,
    build_reference_video_payload,
    build_seedance_payload,
    build_flux_payload,
    build_face_swap_payload,
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
    def __init__(
        self,
        repo: CampaignRepository,
        sogni_client,
        key_store,
        wavespeed_client=None,
        wavespeed_key_store=None,
        wavespeed_campaign_store=None,
        wavespeed_queue_store=None,
        flux_campaign_store=None,
    ) -> None:
        super().__init__()
        self.repo = repo
        self.sogni = sogni_client
        self.key_store = key_store
        self.wavespeed_key_store = wavespeed_key_store or ApiKeyStore(data_dir() / "wavespeed_api_key.txt")
        self.wavespeed = wavespeed_client or WaveSpeedClient(self.wavespeed_key_store.get())
        self.wavespeed_history = WaveSpeedHistoryStore()
        self.wavespeed_queue_store = wavespeed_queue_store or WaveSpeedQueueStore()
        self.wavespeed_campaign_store = wavespeed_campaign_store or WaveSpeedCampaignStore()
        self.flux_campaign_store = flux_campaign_store or FluxCampaignStore()
        self._flux_campaign_id: str | None = None
        self._flux_campaign_name = "Nueva campaña Flux"
        self._flux_campaign_persist_lock = threading.Lock()
        self._flux_history_lock = threading.Lock()
        self._flux_queue_running = False
        self._flux_queue_active: FluxQueue | None = None
        self._wavespeed_campaign_id: str | None = None
        self._wavespeed_campaign_name = "Nueva campaña"
        self._wavespeed_campaign_persist_lock = threading.Lock()
        self._wavespeed_queue_cancel_event = threading.Event()
        self._wavespeed_queue_running = False
        self._wavespeed_queue_active = None
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
        self.tabs.addTab(self._images_tab(), "Imágenes")
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
        self.wavespeed_model_combo = QComboBox()
        self.wavespeed_model_combo.addItem("WAN 3.0 Reference-to-Video", WAN_MODEL_ID)
        self.wavespeed_model_combo.addItem("Seedance 2.0 Text-to-Video", SEEDANCE_MODEL_ID)
        self.wavespeed_model_combo.currentIndexChanged.connect(self._wavespeed_model_changed)
        connection_form.addWidget(QLabel("Modelo"), 2, 0)
        connection_form.addWidget(self.wavespeed_model_combo, 2, 1, 1, 2)
        endpoint = QLabel()
        self.wavespeed_endpoint_label = endpoint
        endpoint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        endpoint.setWordWrap(True)
        connection_form.addWidget(endpoint, 3, 0, 1, 3)
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
        self.wavespeed_reference_labels: dict[str, QLabel] = {}
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
            count_label = QLabel(label)
            self.wavespeed_reference_labels[kind] = count_label
            box_layout.addWidget(count_label)
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

        parameters = QGroupBox("Parámetros del modelo")
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
        self.wavespeed_queue_estimate_button = QPushButton("Estimar costos cola")
        self.wavespeed_queue_estimate_button.clicked.connect(self._estimate_wavespeed_queue_prices)
        self.wavespeed_queue_total_label = QLabel("Total cola: no estimado")
        self.wavespeed_queue_add_button = QPushButton("Agregar a la cola")
        self.wavespeed_queue_add_button.clicked.connect(self._wavespeed_add_current_to_queue)
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
        output_form.addWidget(self.wavespeed_queue_estimate_button, 2, 0)
        output_form.addWidget(self.wavespeed_queue_total_label, 2, 1)
        output_form.addWidget(cancel_polling, 2, 2)
        output_form.addWidget(self.wavespeed_queue_add_button, 3, 0)
        output_form.addWidget(self.wavespeed_task_edit, 4, 0, 1, 2)
        output_form.addWidget(query, 4, 2)
        output_form.addWidget(delete, 5, 2)
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

        self.wavespeed_queue_widget = WaveSpeedQueueWidget()
        self.wavespeed_queue_widget.start_requested.connect(self._start_wavespeed_queue)
        self.wavespeed_queue_widget.pause_requested.connect(self._pause_wavespeed_queue)
        self.wavespeed_queue_widget.retry_requested.connect(self._retry_wavespeed_item)
        self.wavespeed_queue_widget.retry_failed_requested.connect(self._retry_wavespeed_failed)
        self.wavespeed_queue_widget.clear_completed_requested.connect(self._clear_wavespeed_completed)
        self.wavespeed_queue_widget.estimate_requested.connect(self._estimate_wavespeed_queue_prices)
        self.wavespeed_queue_widget.campaign_selected.connect(self._select_wavespeed_campaign)
        self.wavespeed_queue_widget.new_campaign_requested.connect(self._new_wavespeed_campaign)
        self.wavespeed_queue_widget.save_campaign_requested.connect(self._save_wavespeed_campaign)
        self.wavespeed_queue_widget.delete_campaign_requested.connect(self._delete_wavespeed_campaign)
        self.wavespeed_queue_widget.queue_changed.connect(self._save_wavespeed_queue)
        self.wavespeed_campaign_combo = self.wavespeed_queue_widget.campaign_combo
        self.wavespeed_campaign_name_edit = self.wavespeed_queue_widget.campaign_name_edit
        self.wavespeed_campaign_save_button = self.wavespeed_queue_widget.campaign_save_button
        self.wavespeed_campaign_new_button = self.wavespeed_queue_widget.campaign_new_button
        self.wavespeed_execution_mode_combo = self.wavespeed_queue_widget.execution_mode_combo
        self._load_wavespeed_campaigns()
        layout.addWidget(self.wavespeed_queue_widget)

        self.wavespeed_history_table = QTableWidget(0, 6)
        self.wavespeed_history_table.setHorizontalHeaderLabels(["Task", "Modelo", "Estado", "Archivo", "Creado", "Error"])
        self.wavespeed_history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(QLabel("Historial local WaveSpeed"))
        layout.addWidget(self.wavespeed_history_table)

        scroll.setWidget(inner)
        outer.addWidget(scroll)
        self._wavespeed_refresh_history()
        self._wavespeed_update_advanced_controls()
        self._wavespeed_model_changed()
        return page

    def _wavespeed_model_changed(self) -> None:
        seedance = self.wavespeed_model_combo.currentData() == SEEDANCE_MODEL_ID
        model_id = SEEDANCE_MODEL_ID if seedance else WAN_MODEL_ID
        self.wavespeed_endpoint_label.setText(f"Modelo: {model_id}\nEndpoint: {self.wavespeed.BASE_URL}/{model_id}")
        resolution = self.wavespeed_resolution_combo.currentText()
        aspect = self.wavespeed_aspect_combo.currentText()
        self.wavespeed_resolution_combo.clear()
        self.wavespeed_resolution_combo.addItems([*WAVESPEED_RESOLUTIONS, *(["4k"] if seedance else [])])
        self.wavespeed_resolution_combo.setCurrentText(resolution if self.wavespeed_resolution_combo.findText(resolution) >= 0 else "720p")
        self.wavespeed_aspect_combo.clear()
        self.wavespeed_aspect_combo.addItems([*WAVESPEED_ASPECT_RATIOS, *(["21:9"] if seedance else [])])
        self.wavespeed_aspect_combo.setCurrentText(aspect if self.wavespeed_aspect_combo.findText(aspect) >= 0 else "16:9")
        self.wavespeed_duration_spin.setRange(4 if seedance else 2, 15 if seedance else 30)
        for kind, title, limit in (("reference_images", "Imágenes", 9 if seedance else 10),
                                   ("reference_videos", "Videos", 3 if seedance else 5),
                                   ("reference_audios", "Audios", 3 if seedance else 5)):
            self.wavespeed_reference_labels[kind].setText(
                f"{title} ({self.wavespeed_reference_lists[kind].count()}/{limit})")
        self.wavespeed_prompt_expansion_check.setText("enable_web_search" if seedance else "enable_prompt_expansion")
        self.wavespeed_audio_check.setText("generate_audio" if seedance else "enable_audio")
        self.wavespeed_random_seed_check.setVisible(not seedance)
        self.wavespeed_seed_spin.setVisible(not seedance)
        self.wavespeed_sync_check.setVisible(not seedance)
        self.wavespeed_base64_check.setVisible(not seedance)
        self.wavespeed_status_label.setText("Listo. Seedance acepta solo texto o referencias." if seedance else "Listo. Agregá al menos una referencia.")

    def _images_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        image_tabs = QTabWidget()
        image_tabs.addTab(self._face_swap_tab(), "Face Swap")
        image_tabs.addTab(self._flux_tab(), "Flux Edit")
        layout.addWidget(image_tabs)
        return page

    def _face_swap_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(
            "Reemplaza solo el rostro de la imagen objetivo. No usa prompt ni regenera el encuadre."
        ))
        self.face_swap_target_edit = QLineEdit()
        self.face_swap_target_edit.setPlaceholderText("Frame objetivo: se conserva completo salvo el rostro")
        target_button = QPushButton("Elegir frame objetivo")
        target_button.clicked.connect(lambda: self._face_swap_choose_image(self.face_swap_target_edit))
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Imagen objetivo"))
        target_row.addWidget(self.face_swap_target_edit)
        target_row.addWidget(target_button)
        layout.addLayout(target_row)
        self.face_swap_identity_edit = QLineEdit()
        self.face_swap_identity_edit.setPlaceholderText("Retrato frontal limpio de la identidad fuente")
        identity_button = QPushButton("Elegir rostro fuente")
        identity_button.clicked.connect(lambda: self._face_swap_choose_image(self.face_swap_identity_edit))
        identity_row = QHBoxLayout()
        identity_row.addWidget(QLabel("Rostro fuente"))
        identity_row.addWidget(self.face_swap_identity_edit)
        identity_row.addWidget(identity_button)
        layout.addLayout(identity_row)
        options = QGridLayout()
        self.face_swap_target_index = QSpinBox()
        self.face_swap_target_index.setRange(0, 10)
        self.face_swap_gender_combo = QComboBox()
        self.face_swap_gender_combo.addItem("Mujer", "female")
        self.face_swap_gender_combo.addItem("Todos", "all")
        self.face_swap_gender_combo.addItem("Hombre", "male")
        self.face_swap_format_combo = QComboBox()
        self.face_swap_format_combo.addItems(["png", "jpeg", "webp"])
        options.addWidget(QLabel("Rostro objetivo (0 = rostro más grande)"), 0, 0)
        options.addWidget(self.face_swap_target_index, 0, 1)
        options.addWidget(QLabel("Género objetivo"), 1, 0)
        options.addWidget(self.face_swap_gender_combo, 1, 1)
        options.addWidget(QLabel("Formato"), 2, 0)
        options.addWidget(self.face_swap_format_combo, 2, 1)
        layout.addLayout(options)
        self.face_swap_output_edit = QLineEdit(str(data_dir() / "wavespeed_outputs"))
        output_button = QPushButton("Carpeta")
        output_button.clicked.connect(lambda: self._face_swap_choose_output(self.face_swap_output_edit))
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Salida"))
        output_row.addWidget(self.face_swap_output_edit)
        output_row.addWidget(output_button)
        layout.addLayout(output_row)
        self.face_swap_generate_button = QPushButton("Generar Face Swap")
        self.face_swap_generate_button.clicked.connect(self._face_swap_generate)
        layout.addWidget(self.face_swap_generate_button)
        self.face_swap_status_label = QLabel("Listo. Elegí el frame y un retrato frontal de la identidad.")
        layout.addWidget(self.face_swap_status_label)
        self.face_swap_raw_edit = QTextEdit()
        self.face_swap_raw_edit.setReadOnly(True)
        layout.addWidget(self.face_swap_raw_edit)
        layout.addStretch()
        return page

    def _flux_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(f"{FLUX_MODEL_ID} · edición de 1 a 3 imágenes con la clave WaveSpeed guardada"))
        self.flux_prompt_edit = QTextEdit()
        self.flux_prompt_edit.setPlaceholderText("Describe la edición. Usa image1, image2, image3 para referirte a las entradas.")
        layout.addWidget(QLabel("Prompt"))
        layout.addWidget(self.flux_prompt_edit)
        self.flux_images = QListWidget()
        self.flux_images.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(QLabel("Imágenes de entrada (1–3)"))
        layout.addWidget(self.flux_images)
        image_buttons = QHBoxLayout()
        add = QPushButton("Agregar imágenes")
        add.clicked.connect(self._flux_add_images)
        remove = QPushButton("Quitar seleccionadas")
        remove.clicked.connect(self._flux_remove_images)
        move_up = QPushButton("↑")
        move_up.setToolTip("Subir la imagen seleccionada: pasa a ser image1/image2 antes")
        move_up.clicked.connect(lambda: self._flux_move_image(-1))
        move_down = QPushButton("↓")
        move_down.setToolTip("Bajar la imagen seleccionada: pasa a ser image2/image3 después")
        move_down.clicked.connect(lambda: self._flux_move_image(1))
        image_buttons.addWidget(add)
        image_buttons.addWidget(remove)
        image_buttons.addWidget(move_up)
        image_buttons.addWidget(move_down)
        layout.addLayout(image_buttons)
        options = QGridLayout()
        self.flux_size_edit = QLineEdit()
        self.flux_size_edit.setPlaceholderText("Vacío = tamaño de entrada; ej. 1024*1024")
        self.flux_random_seed_check = QCheckBox("Seed aleatorio (-1)")
        self.flux_random_seed_check.setChecked(True)
        self.flux_seed_spin = QSpinBox()
        self.flux_seed_spin.setRange(0, 2_147_483_647)
        self.flux_seed_spin.setDisabled(True)
        self.flux_random_seed_check.toggled.connect(self.flux_seed_spin.setDisabled)
        self.flux_sync_check = QCheckBox("enable_sync_mode")
        self.flux_base64_check = QCheckBox("enable_base64_output")
        options.addWidget(QLabel("Size"), 0, 0)
        options.addWidget(self.flux_size_edit, 0, 1)
        options.addWidget(self.flux_random_seed_check, 1, 0)
        options.addWidget(self.flux_seed_spin, 1, 1)
        options.addWidget(self.flux_sync_check, 2, 0)
        options.addWidget(self.flux_base64_check, 2, 1)
        layout.addLayout(options)
        output = QHBoxLayout()
        self.flux_output_edit = QLineEdit(str(data_dir() / "wavespeed_outputs"))
        choose = QPushButton("Carpeta")
        choose.clicked.connect(self._flux_choose_output)
        output.addWidget(QLabel("Output"))
        output.addWidget(self.flux_output_edit)
        output.addWidget(choose)
        layout.addLayout(output)
        controls = QHBoxLayout()
        self.flux_price_label = QLabel("Precio: no estimado")
        estimate = QPushButton("Estimar precio")
        estimate.clicked.connect(self._flux_estimate_price)
        self.flux_generate_button = QPushButton("Generar imagen")
        self.flux_generate_button.clicked.connect(self._flux_generate)
        self.flux_queue_add_button = QPushButton("Agregar a la cola")
        self.flux_queue_add_button.clicked.connect(self._flux_add_current_to_queue)
        controls.addWidget(estimate)
        controls.addWidget(self.flux_price_label)
        controls.addWidget(self.flux_generate_button)
        controls.addWidget(self.flux_queue_add_button)
        layout.addLayout(controls)
        self.flux_status_label = QLabel("Listo.")
        layout.addWidget(self.flux_status_label)
        self.flux_task_edit = QLineEdit()
        self.flux_task_edit.setPlaceholderText("Task ID para consultar")
        query = QPushButton("Consultar tarea")
        query.clicked.connect(self._flux_query_task)
        task_row = QHBoxLayout()
        task_row.addWidget(self.flux_task_edit)
        task_row.addWidget(query)
        layout.addLayout(task_row)
        self.flux_raw_edit = QTextEdit()
        self.flux_raw_edit.setReadOnly(True)
        layout.addWidget(self.flux_raw_edit)
        self.flux_queue_widget = FluxQueueWidget()
        self.flux_queue_widget.queue_changed.connect(self._save_flux_queue)
        self.flux_queue_widget.campaign_selected.connect(self._select_flux_campaign)
        self.flux_queue_widget.new_campaign_requested.connect(self._new_flux_campaign)
        self.flux_queue_widget.save_campaign_requested.connect(self._save_flux_queue)
        self.flux_queue_widget.delete_campaign_requested.connect(self._delete_flux_campaign)
        self.flux_queue_widget.start_requested.connect(self._start_flux_queue)
        self.flux_queue_widget.pause_requested.connect(self._pause_flux_queue)
        self.flux_queue_widget.retry_requested.connect(self._retry_flux_item)
        self.flux_queue_widget.retry_failed_requested.connect(self._retry_flux_failed)
        self.flux_queue_widget.clear_completed_requested.connect(self._clear_flux_completed)
        self.flux_queue_widget.estimate_requested.connect(self._estimate_flux_queue_prices)
        layout.addWidget(self.flux_queue_widget)
        self._load_flux_campaigns()
        return page

    def _flux_add_images(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(self, "Seleccionar imágenes", filter="Images (*.png *.jpg *.jpeg *.webp *.gif)")
        existing = {self.flux_images.item(i).data(Qt.UserRole) for i in range(self.flux_images.count())}
        for name in selected:
            path = str(Path(name).resolve())
            if path in existing or self.flux_images.count() >= 3:
                continue
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            self.flux_images.addItem(item)
            existing.add(path)

    def _flux_remove_images(self) -> None:
        for item in self.flux_images.selectedItems():
            self.flux_images.takeItem(self.flux_images.row(item))

    def _flux_move_image(self, direction: int) -> None:
        source = self.flux_images.currentRow()
        destination = source + direction
        if source < 0 or not 0 <= destination < self.flux_images.count():
            return
        item = self.flux_images.takeItem(source)
        self.flux_images.insertItem(destination, item)
        self.flux_images.setCurrentRow(destination)

    def _flux_choose_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de salida")
        if selected:
            self.flux_output_edit.setText(selected)

    def _face_swap_choose_image(self, edit: QLineEdit) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar imagen", filter="Images (*.png *.jpg *.jpeg *.webp *.gif)"
        )
        if selected:
            edit.setText(str(Path(selected).resolve()))

    def _face_swap_choose_output(self, edit: QLineEdit) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de salida")
        if selected:
            edit.setText(selected)

    def _face_swap_snapshot(self) -> dict:
        return {
            "image": self.face_swap_target_edit.text().strip(),
            "face_image": self.face_swap_identity_edit.text().strip(),
            "target_index": self.face_swap_target_index.value(),
            "target_gender": self.face_swap_gender_combo.currentData(),
            "output_format": self.face_swap_format_combo.currentText(),
            "enable_sync_mode": False,
            "enable_base64_output": False,
            "output_dir": self.face_swap_output_edit.text().strip(),
        }

    def _face_swap_upload_and_build(self, snapshot: dict) -> dict:
        build_face_swap_payload(**{key: snapshot[key] for key in (
            "image", "face_image", "target_index", "target_gender", "output_format",
            "enable_sync_mode", "enable_base64_output",
        )})
        target = Path(snapshot["image"])
        identity = Path(snapshot["face_image"])
        validate_local_reference_file(target, "reference_images")
        validate_local_reference_file(identity, "reference_images")
        return build_face_swap_payload(
            image=self._wavespeed_upload_cached(target)["download_url"],
            face_image=self._wavespeed_upload_cached(identity)["download_url"],
            target_index=snapshot["target_index"], target_gender=snapshot["target_gender"],
            output_format=snapshot["output_format"],
        )

    def _face_swap_generate(self) -> None:
        snapshot = self._face_swap_snapshot()
        self.face_swap_generate_button.setEnabled(False)
        self.face_swap_status_label.setText("Preparando Face Swap...")

        def task():
            payload = self._face_swap_upload_and_build(snapshot)
            prediction = self.wavespeed.submit(payload, model_id=FACE_SWAP_MODEL_ID)
            if prediction.status not in {"completed", "failed", "cancelled", "timeout", "deleted"}:
                prediction = self.wavespeed.poll_result(prediction.id)
            output_file = ""
            if prediction.status == "completed" and prediction.outputs:
                output_file = self._wavespeed_save_output(
                    prediction.outputs[0], snapshot, prediction.id, suffix=f".{snapshot['output_format']}"
                )
            self.wavespeed_history.add({
                "task_id": prediction.id, "model_id": FACE_SWAP_MODEL_ID, "status": prediction.status,
                "output_file": output_file, "error": prediction.error, "payload": payload,
                "created_at": prediction.created_at,
            })
            return {"prediction": prediction, "output_file": output_file}

        self._run_wavespeed_worker(task, "face_swap_generate")

    def _flux_snapshot(self) -> dict:
        return {
            "prompt": self.flux_prompt_edit.toPlainText(),
            "images": [self.flux_images.item(i).data(Qt.UserRole) for i in range(self.flux_images.count())],
            "size": self.flux_size_edit.text().strip(),
            "seed": -1 if self.flux_random_seed_check.isChecked() else self.flux_seed_spin.value(),
            "enable_sync_mode": self.flux_sync_check.isChecked(),
            "enable_base64_output": self.flux_base64_check.isChecked(),
            "output_dir": self.flux_output_edit.text().strip(),
        }

    def _flux_upload_and_build(self, snapshot: dict) -> dict:
        # Validate before any upload or paid submission.
        build_flux_payload(**{key: snapshot[key] for key in
                              ("prompt", "images", "size", "seed", "enable_sync_mode", "enable_base64_output")})
        urls = []
        for name in snapshot["images"]:
            validate_local_reference_file(Path(name), "reference_images")
            urls.append(self._wavespeed_upload_cached(Path(name))["download_url"])
        return build_flux_payload(prompt=snapshot["prompt"], images=urls, size=snapshot["size"],
                                  seed=snapshot["seed"], enable_sync_mode=snapshot["enable_sync_mode"],
                                  enable_base64_output=snapshot["enable_base64_output"])

    def _flux_estimate_price(self) -> None:
        snapshot = self._flux_snapshot()
        self.flux_price_label.setText("Calculando...")
        self._run_wavespeed_worker(lambda: self.wavespeed.estimate_price(
            self._flux_upload_and_build(snapshot), model_id=FLUX_MODEL_ID), "flux_price")

    def _flux_generate(self) -> None:
        snapshot = self._flux_snapshot()
        self.flux_generate_button.setEnabled(False)
        self.flux_status_label.setText("Preparando generación...")

        def task():
            payload = self._flux_upload_and_build(snapshot)
            prediction = self.wavespeed.submit(payload, model_id=FLUX_MODEL_ID)
            if prediction.status not in {"completed", "failed", "cancelled", "timeout", "deleted"}:
                prediction = self.wavespeed.poll_result(prediction.id)
            output_file = ""
            if prediction.status == "completed" and prediction.outputs:
                output_file = self._wavespeed_save_output(prediction.outputs[0], snapshot, prediction.id, suffix=".png")
            self.wavespeed_history.add({"task_id": prediction.id, "model_id": FLUX_MODEL_ID,
                                        "status": prediction.status, "output_file": output_file,
                                        "error": prediction.error, "payload": payload,
                                        "created_at": prediction.created_at})
            return {"prediction": prediction, "output_file": output_file}

        self._run_wavespeed_worker(task, "flux_generate")

    def _flux_query_task(self) -> None:
        task_id = self.flux_task_edit.text().strip()
        if task_id:
            self._run_wavespeed_worker(lambda: self.wavespeed.get_result(task_id), "flux_query")

    def _load_flux_campaigns(self) -> None:
        try:
            campaigns, active_id = self.flux_campaign_store.load()
        except ValueError as exc:
            self.flux_queue_widget.status_label.setText(f"Error: {exc}")
            return
        self.flux_queue_widget.set_campaigns(campaigns, active_id)
        if campaigns:
            self._select_flux_campaign(active_id or campaigns[0].campaign_id)
        else:
            self._flux_campaign_id = None
            self._flux_campaign_name = "Nueva campaña Flux"
            self.flux_queue_widget.campaign_name_edit.setText(self._flux_campaign_name)
            self.flux_queue_widget.load_queue(FluxQueue())

    def _select_flux_campaign(self, campaign_id: str) -> None:
        if not campaign_id or self._flux_queue_running:
            return
        if self._flux_campaign_id and self._flux_campaign_id != campaign_id:
            if not self._save_flux_queue():
                return
        try:
            campaigns, active_id = self.flux_campaign_store.load()
        except ValueError as exc:
            self.flux_queue_widget.status_label.setText(f"Error: {exc}")
            return
        campaign = next((row for row in campaigns if row.campaign_id == campaign_id), None)
        if campaign is None:
            return
        if active_id != campaign_id:
            self.flux_campaign_store.save(campaign, active=True)
        self._flux_campaign_id = campaign.campaign_id
        self._flux_campaign_name = campaign.name
        campaigns, _ = self.flux_campaign_store.load()
        self.flux_queue_widget.set_campaigns(campaigns, campaign.campaign_id)
        self.flux_queue_widget.campaign_name_edit.setText(campaign.name)
        self.flux_queue_widget.load_queue(campaign.queue)
        self.flux_output_edit.setText(campaign.queue.output_dir or str(data_dir() / "wavespeed_outputs"))

    def _save_flux_queue(self) -> bool:
        if self._flux_queue_running or not hasattr(self, "flux_queue_widget"):
            return False
        name = self.flux_queue_widget.campaign_name_edit.text().strip() or "Nueva campaña Flux"
        campaign = FluxCampaign(campaign_id=self._flux_campaign_id or FluxCampaign().campaign_id,
                                name=name, queue=self.flux_queue_widget.queue())
        try:
            self.flux_campaign_store.save(campaign, active=True)
        except ValueError as exc:
            self.flux_queue_widget.status_label.setText(f"Error: {exc}")
            return False
        self._flux_campaign_id = campaign.campaign_id
        self._flux_campaign_name = name
        campaigns, _ = self.flux_campaign_store.load()
        self.flux_queue_widget.set_campaigns(campaigns, campaign.campaign_id)
        return True

    def _persist_flux_campaign_queue(self, queue: FluxQueue) -> None:
        if not self._flux_campaign_id:
            return
        with self._flux_campaign_persist_lock:
            campaign = FluxCampaign(campaign_id=self._flux_campaign_id,
                                    name=self._flux_campaign_name, queue=queue)
            self.flux_campaign_store.save(campaign, active=True)

    def _new_flux_campaign(self) -> None:
        if self._flux_queue_running:
            return
        if self._flux_campaign_id:
            if not self._save_flux_queue():
                return
        campaign = FluxCampaign()
        try:
            self.flux_campaign_store.save(campaign, active=True)
        except ValueError as exc:
            self.flux_queue_widget.status_label.setText(f"Error: {exc}")
            return
        self._load_flux_campaigns()

    def _delete_flux_campaign(self) -> None:
        if self._flux_queue_running or not self._flux_campaign_id:
            return
        try:
            self.flux_campaign_store.delete(self._flux_campaign_id)
        except ValueError as exc:
            self.flux_queue_widget.status_label.setText(f"Error: {exc}")
            return
        self._flux_campaign_id = None
        self._load_flux_campaigns()

    def _flux_add_current_to_queue(self) -> None:
        if self._flux_queue_running:
            return
        snapshot = self._flux_snapshot()
        try:
            build_flux_payload(**{key: snapshot[key] for key in
                                  ("prompt", "images", "size", "seed", "enable_sync_mode", "enable_base64_output")})
            for name in snapshot["images"]:
                validate_local_reference_file(Path(name), "reference_images")
        except Exception as exc:
            self.flux_queue_widget.status_label.setText(f"Error: {exc}")
            return
        queue = self.flux_queue_widget.queue()
        queue.items.append(FluxQueueItem(prompt=snapshot["prompt"], image_paths=list(snapshot["images"]),
                                         size=snapshot["size"], seed=snapshot["seed"],
                                         enable_sync_mode=snapshot["enable_sync_mode"],
                                         enable_base64_output=snapshot["enable_base64_output"]))
        queue.output_dir = snapshot["output_dir"]
        self.flux_queue_widget.load_queue(queue)
        if self._save_flux_queue():
            self.flux_queue_widget.status_label.setText(f"{len(queue.items)} ítem(s) en cola.")

    @staticmethod
    def _validate_flux_queue(queue: FluxQueue) -> None:
        if not queue.items:
            raise ValueError("Agregá al menos una edición a la cola.")
        for item in queue.items:
            if item.task_id:
                continue
            build_flux_payload(prompt=item.prompt, images=item.image_paths, size=item.size, seed=item.seed,
                               enable_sync_mode=item.enable_sync_mode,
                               enable_base64_output=item.enable_base64_output)
            for name in item.image_paths:
                validate_local_reference_file(Path(name), "reference_images")

    def _start_flux_queue(self) -> None:
        if self._flux_queue_running:
            return
        queue = self.flux_queue_widget.queue()
        try:
            self._validate_flux_queue(queue)
        except Exception as exc:
            self.flux_queue_widget.status_label.setText(f"Error: {exc}")
            return
        if not self._flux_campaign_id and not self._save_flux_queue():
            return
        queue.paused = False
        try:
            self._persist_flux_campaign_queue(queue)
        except Exception as exc:
            self.flux_queue_widget.status_label.setText(f"Error al guardar la cola: {exc}")
            return
        self._flux_queue_running = True
        self._flux_queue_active = queue
        self.flux_queue_widget.set_running(True)
        self.flux_queue_add_button.setEnabled(False)
        self.flux_queue_widget.status_label.setText("Cola Flux iniciada.")
        threading.Thread(target=self._run_flux_queue, args=(queue,), daemon=True).start()

    def _run_flux_queue(self, queue: FluxQueue) -> None:
        runner = FluxQueueRunner(
            self.wavespeed, upload_file=self._wavespeed_upload_cached,
            save_output=lambda output, output_dir, task_id: self._wavespeed_save_output(
                output, {"output_dir": output_dir}, task_id, suffix=".png"),
            on_update=lambda item: self._flux_queue_item_update(queue, item),
        )
        error = None
        try:
            runner.run(queue)
        except Exception as exc:
            error = exc
        finally:
            self._persist_flux_campaign_queue(queue)
            self._post_wavespeed_event("flux_queue_finished", error)

    def _flux_queue_item_update(self, queue: FluxQueue, item: FluxQueueItem) -> None:
        self._persist_flux_campaign_queue(queue)
        if item.task_id and item.status in {QueueStatus.COMPLETED, QueueStatus.FAILED}:
            with self._flux_history_lock:
                self.wavespeed_history.add({
                    "task_id": item.task_id, "model_id": FLUX_MODEL_ID, "status": item.status,
                    "output_file": item.output_file, "error": item.error,
                    "payload": {"prompt": item.prompt, "image_paths": item.image_paths,
                                "size": item.size, "seed": item.seed},
                    "created_at": item.created_at,
                })
        self._post_wavespeed_event("flux_queue_item", item)

    def _pause_flux_queue(self) -> None:
        if not self._flux_queue_running or self._flux_queue_active is None:
            return
        self._flux_queue_active.paused = True
        self._persist_flux_campaign_queue(self._flux_queue_active)
        self.flux_queue_widget.status_label.setText("Se pausará después de los ítems en curso.")

    def _retry_flux_item(self, item_id: str) -> None:
        if self._flux_queue_running:
            return
        queue = self.flux_queue_widget.queue()
        item = next((row for row in queue.items if row.item_id == item_id), None)
        if item is None or item.status == QueueStatus.RUNNING:
            return
        item.retry()
        queue.paused = False
        self.flux_queue_widget.set_item_update(item)
        self._persist_flux_campaign_queue(queue)
        self._start_flux_queue()

    def _retry_flux_failed(self) -> None:
        if self._flux_queue_running:
            return
        queue = self.flux_queue_widget.queue()
        for item in queue.items:
            if item.status == QueueStatus.FAILED:
                item.retry()
                self.flux_queue_widget.set_item_update(item)
        queue.paused = False
        self._persist_flux_campaign_queue(queue)
        self._start_flux_queue()

    def _clear_flux_completed(self) -> None:
        if self._flux_queue_running:
            return
        queue = self.flux_queue_widget.queue()
        queue.items = [item for item in queue.items if item.status != QueueStatus.COMPLETED]
        self.flux_queue_widget.load_queue(queue)
        self._persist_flux_campaign_queue(queue)

    def _estimate_flux_queue_prices(self) -> None:
        if self._flux_queue_running:
            return
        queue = self.flux_queue_widget.queue()
        try:
            self._validate_flux_queue(queue)
        except Exception as exc:
            self.flux_queue_widget.set_total_price(None, str(exc))
            return
        self.flux_queue_widget.estimate_button.setEnabled(False)
        self.flux_queue_widget.total_price_label.setText("Total estimado: calculando...")

        def task():
            runner = FluxQueueRunner(self.wavespeed, upload_file=self._wavespeed_upload_cached,
                                     save_output=lambda *_args: "",
                                     on_update=lambda item: self._flux_queue_item_update(queue, item))
            return runner.estimate_prices(queue)

        self._run_wavespeed_worker(task, "flux_queue_prices")

    def _wavespeed_add_files(self, kind: str, file_filter: str) -> None:
        selected, _ = QFileDialog.getOpenFileNames(self, "Seleccionar referencias", filter=file_filter)
        limits = ({"reference_images": 9, "reference_videos": 3, "reference_audios": 3}
                  if self.wavespeed_model_combo.currentData() == SEEDANCE_MODEL_ID else
                  {"reference_images": 10, "reference_videos": 5, "reference_audios": 5})
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
        title = {"reference_images": "Imágenes", "reference_videos": "Videos", "reference_audios": "Audios"}[kind]
        self.wavespeed_reference_labels[kind].setText(f"{title} ({widget.count()}/{limits[kind]})")

    def _wavespeed_add_current_to_queue(self) -> None:
        image_paths = [
            str(self.wavespeed_reference_lists["reference_images"].item(index).data(Qt.UserRole))
            for index in range(self.wavespeed_reference_lists["reference_images"].count())
        ]
        video_paths = [
            str(self.wavespeed_reference_lists["reference_videos"].item(index).data(Qt.UserRole))
            for index in range(self.wavespeed_reference_lists["reference_videos"].count())
        ]
        if len(image_paths) != 1:
            self.wavespeed_status_label.setText("Seleccioná exactamente un frame para la cola.")
            return
        if not video_paths:
            self.wavespeed_status_label.setText("Agregá al menos un video antes de enviarlo a la cola.")
            return

        frame_path = image_paths[0]
        queue = self.wavespeed_queue_widget.queue()
        if queue.frame_path and Path(queue.frame_path).name.casefold() != Path(frame_path).name.casefold():
            self.wavespeed_status_label.setText(
                f"El frame {Path(frame_path).name} es distinto al de la cola ({Path(queue.frame_path).name})."
            )
            return

        snapshot = self._wavespeed_snapshot()
        queue.frame_path = frame_path
        queue.output_dir = snapshot["output_dir"]
        existing_videos = {str(Path(item.video_path).resolve()).casefold() for item in queue.items}
        added_count = 0
        for video_path in video_paths:
            if str(Path(video_path).resolve()).casefold() in existing_videos:
                continue
            queue.items.append(
                WaveSpeedQueueItem(
                    video_path=video_path,
                    prompt=snapshot["prompt"],
                    resolution=snapshot["resolution"],
                    aspect_ratio=snapshot["aspect_ratio"],
                    duration=snapshot["duration"],
                    enable_prompt_expansion=snapshot["enable_prompt_expansion"],
                    enable_audio=snapshot["enable_audio"],
                    seed=snapshot["seed"],
                    model_id=snapshot["model_id"],
                )
            )
            existing_videos.add(str(Path(video_path).resolve()).casefold())
            added_count += 1
        self.wavespeed_queue_widget.load_queue(queue)
        self._save_wavespeed_queue()
        self.wavespeed_status_label.setText(
            f"{added_count} job(s) agregado(s) a la cola del frame {Path(frame_path).name}."
        )

    def _wavespeed_remove_files(self, kind: str) -> None:
        widget = self.wavespeed_reference_lists[kind]
        for item in widget.selectedItems():
            widget.takeItem(widget.row(item))
        self._wavespeed_model_changed()

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
            "model_id": self.wavespeed_model_combo.currentData(),
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
        if snapshot["model_id"] == SEEDANCE_MODEL_ID:
            build_seedance_payload(prompt=snapshot["prompt"],
                reference_images=snapshot["reference_images"], reference_videos=snapshot["reference_videos"],
                reference_audios=snapshot["reference_audios"], resolution=snapshot["resolution"],
                aspect_ratio=snapshot["aspect_ratio"], duration=snapshot["duration"],
                enable_web_search=snapshot["enable_prompt_expansion"], generate_audio=snapshot["enable_audio"])
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
        common = dict(prompt=snapshot["prompt"], reference_images=snapshot["reference_images"],
                      reference_videos=snapshot["reference_videos"], reference_audios=snapshot["reference_audios"],
                      resolution=snapshot["resolution"], aspect_ratio=snapshot["aspect_ratio"],
                      duration=snapshot["duration"])
        if snapshot["model_id"] == SEEDANCE_MODEL_ID:
            payload = build_seedance_payload(**common,
                enable_web_search=snapshot["enable_prompt_expansion"], generate_audio=snapshot["enable_audio"])
        else:
            payload = build_reference_video_payload(**common,
                enable_prompt_expansion=snapshot["enable_prompt_expansion"], enable_audio=snapshot["enable_audio"],
                seed=snapshot["seed"], enable_sync_mode=snapshot["enable_sync_mode"],
                enable_base64_output=snapshot["enable_base64_output"])
        return payload, uploaded

    def _wavespeed_generate(self) -> None:
        snapshot = self._wavespeed_snapshot()
        self._wavespeed_start_busy("Preparando generación...")
        self._wavespeed_cancel_event.clear()

        def task():
            payload, _uploaded = self._wavespeed_upload_and_build(snapshot)
            prediction = self.wavespeed.submit(payload, webhook_url=snapshot["webhook_url"] or None,
                                               model_id=snapshot["model_id"])
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
                "model_id": snapshot["model_id"],
                "created_at": prediction.created_at,
            })
            return {"prediction": prediction, "output_file": output_file, "payload": payload}

        self._run_wavespeed_worker(task, "generate")

    def _wavespeed_save_output(self, output: object, snapshot: dict, task_id: str, suffix: str = ".mp4") -> str:
        output_dir = Path(snapshot["output_dir"] or data_dir() / "wavespeed_outputs")
        if suffix == ".png" and isinstance(output, str) and output.startswith(("http://", "https://")):
            remote_suffix = Path(urlparse(output).path).suffix.lower()
            if remote_suffix in {".png", ".jpg", ".jpeg", ".webp"}:
                suffix = remote_suffix
        destination = output_dir / f"wavespeed_{task_id}{suffix}"
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

    def _load_wavespeed_campaigns(self) -> None:
        campaigns, active_id = self.wavespeed_campaign_store.load()
        legacy_queue = self.wavespeed_queue_store.load()
        if not campaigns:
            if legacy_queue.frame_path or legacy_queue.items:
                migrated = WaveSpeedCampaign(name="Campaña importada", queue=legacy_queue)
                self.wavespeed_campaign_store.save(migrated, active=True)
                campaigns, active_id = [migrated], migrated.campaign_id
        if not campaigns:
            first = WaveSpeedCampaign()
            self.wavespeed_campaign_store.save(first, active=True)
            campaigns, active_id = [first], first.campaign_id
        if active_id not in {campaign.campaign_id for campaign in campaigns}:
            active_id = campaigns[0].campaign_id
        active_campaign = next((campaign for campaign in campaigns if campaign.campaign_id == active_id), None)
        can_recover_legacy = active_campaign is not None and (
            active_campaign.name == "Campaña importada"
            or (len(campaigns) == 1 and active_campaign.name == "Nueva campaña")
        )
        if (
            can_recover_legacy
            and not active_campaign.queue.frame_path
            and not active_campaign.queue.items
            and (legacy_queue.frame_path or legacy_queue.items)
        ):
            active_campaign.queue = legacy_queue
            self.wavespeed_campaign_store.save(active_campaign, active=True)
            campaigns, active_id = self.wavespeed_campaign_store.load()
        self.wavespeed_queue_widget.set_campaigns(campaigns, active_id)
        self._select_wavespeed_campaign(active_id)

    def _select_wavespeed_campaign(self, campaign_id: str) -> None:
        if not campaign_id or self._wavespeed_queue_running:
            return
        if self._wavespeed_campaign_id:
            self._save_wavespeed_queue()
        campaigns, _active_id = self.wavespeed_campaign_store.load()
        campaign = next((item for item in campaigns if item.campaign_id == campaign_id), None)
        if campaign is None:
            return
        self._wavespeed_campaign_id = campaign.campaign_id
        self._wavespeed_campaign_name = campaign.name
        self.wavespeed_queue_widget.set_campaign_name(campaign.name)
        self.wavespeed_queue_widget.load_queue(campaign.queue)

    def _save_wavespeed_campaign(self) -> None:
        if self._wavespeed_queue_running:
            return
        self._save_wavespeed_queue()
        self.wavespeed_queue_widget.set_error(f"Campaña guardada: {self._wavespeed_campaign_name}.")

    def _new_wavespeed_campaign(self) -> None:
        if self._wavespeed_queue_running:
            return
        self._save_wavespeed_queue()
        campaign = WaveSpeedCampaign()
        self.wavespeed_campaign_store.save(campaign, active=True)
        self._load_wavespeed_campaigns()

    def _delete_wavespeed_campaign(self) -> None:
        if self._wavespeed_queue_running or not self._wavespeed_campaign_id:
            return
        self.wavespeed_campaign_store.delete(self._wavespeed_campaign_id)
        self._wavespeed_campaign_id = None
        self._load_wavespeed_campaigns()

    def _save_wavespeed_queue(self) -> None:
        if not hasattr(self, "wavespeed_queue_widget"):
            return
        queue = self.wavespeed_queue_widget.queue()
        name = self.wavespeed_queue_widget.campaign_name()
        if self._wavespeed_campaign_id:
            campaign = WaveSpeedCampaign(
                campaign_id=self._wavespeed_campaign_id,
                name=name,
                queue=queue,
            )
        else:
            campaign = WaveSpeedCampaign(name=name, queue=queue)
        self.wavespeed_campaign_store.save(campaign, active=True)
        self._wavespeed_campaign_id = campaign.campaign_id
        self._wavespeed_campaign_name = campaign.name

    def _persist_wavespeed_campaign_queue(self, queue) -> None:
        if not self._wavespeed_campaign_id:
            return
        with self._wavespeed_campaign_persist_lock:
            campaign = WaveSpeedCampaign(
                campaign_id=self._wavespeed_campaign_id,
                name=self._wavespeed_campaign_name,
                queue=queue,
            )
            self.wavespeed_campaign_store.save(campaign, active=True)

    def _validate_wavespeed_queue(self, queue) -> None:
        if not queue.frame_path:
            raise ValueError("Seleccioná un frame compartido.")
        validate_local_reference_file(Path(queue.frame_path), "reference_images")
        if not queue.items:
            raise ValueError("Agregá al menos un video a la cola.")
        for item in queue.items:
            validate_local_reference_file(Path(item.video_path), "reference_videos")
            if not item.prompt.strip():
                raise ValueError(f"El ítem {item.item_id} no tiene prompt.")
            if item.model_id == SEEDANCE_MODEL_ID:
                build_seedance_payload(prompt=item.prompt, reference_images=["frame"],
                    reference_videos=["video"], resolution=item.resolution,
                    aspect_ratio=item.aspect_ratio, duration=item.duration,
                    enable_web_search=item.enable_prompt_expansion, generate_audio=item.enable_audio)
            elif item.model_id != WAN_MODEL_ID:
                raise ValueError(f"Modelo de cola no admitido: {item.model_id}")

    def _start_wavespeed_queue(self) -> None:
        if self._wavespeed_queue_running:
            return
        queue = self.wavespeed_queue_widget.queue()
        try:
            self._validate_wavespeed_queue(queue)
        except Exception as exc:
            self.wavespeed_queue_widget.set_error(str(exc))
            return

        queue.paused = False
        self._wavespeed_queue_active = queue
        self._wavespeed_queue_cancel_event.clear()
        self._wavespeed_queue_running = True
        self.wavespeed_queue_widget.set_running(True)
        self._persist_wavespeed_campaign_queue(queue)
        self.wavespeed_queue_widget.set_error("Cola iniciada.")
        thread = threading.Thread(target=self._run_wavespeed_queue, args=(queue,), daemon=True)
        thread.start()

    def _estimate_wavespeed_queue_prices(self) -> None:
        if self._wavespeed_queue_running:
            return
        queue = self.wavespeed_queue_widget.queue()
        try:
            self._validate_wavespeed_queue(queue)
        except Exception as exc:
            self.wavespeed_queue_total_label.setText(f"Total cola: error: {exc}")
            self.wavespeed_queue_widget.set_total_price(None, str(exc))
            return

        self.wavespeed_queue_estimate_button.setEnabled(False)
        self.wavespeed_queue_total_label.setText("Total cola: calculando...")
        self.wavespeed_queue_widget.set_estimating(True)
        self.wavespeed_queue_widget.set_error("Estimando costos...")

        def task():
            runner = WaveSpeedQueueRunner(
                self.wavespeed,
                upload_file=self._wavespeed_upload_cached,
                save_output=lambda *_args: "",
                on_update=lambda item: self._wavespeed_queue_item_update(queue, item),
            )
            total = runner.estimate_prices(queue)
            self._persist_wavespeed_campaign_queue(queue)
            return total

        self._run_wavespeed_worker(task, "queue_prices")

    def _run_wavespeed_queue(self, queue) -> None:
        runner = WaveSpeedQueueRunner(
            self.wavespeed,
            upload_file=self._wavespeed_upload_cached,
            save_output=lambda output, output_dir, task_id: self._wavespeed_save_output(
                output,
                {"output_dir": output_dir},
                task_id,
            ),
            on_update=lambda item: self._wavespeed_queue_item_update(queue, item),
        )
        error = None
        try:
            runner.run(
                queue,
                cancel_event=self._wavespeed_queue_cancel_event,
                parallel=queue.execution_mode == "parallel",
            )
        except Exception as exc:
            error = exc
        finally:
            self._persist_wavespeed_campaign_queue(queue)
            self._post_wavespeed_event("queue_finished", error)

    def _wavespeed_queue_item_update(self, queue, item: WaveSpeedQueueItem) -> None:
        self._persist_wavespeed_campaign_queue(queue)
        self._post_wavespeed_event("queue_item", item)

    def _wavespeed_upload_cached(self, path: Path) -> dict:
        cache_key = str(Path(path).resolve())
        cached = self._wavespeed_uploaded.get(cache_key)
        if cached:
            return cached
        self._post_wavespeed_event("status", f"Subiendo {Path(path).name}...")
        media = self.wavespeed.upload_file(Path(path))
        self._wavespeed_uploaded[cache_key] = media
        return media

    def _pause_wavespeed_queue(self) -> None:
        if not self._wavespeed_queue_running or self._wavespeed_queue_active is None:
            return
        self._wavespeed_queue_active.paused = True
        self._persist_wavespeed_campaign_queue(self._wavespeed_queue_active)
        self.wavespeed_queue_widget.set_error("La cola se pausará después del ítem actual.")

    def _retry_wavespeed_item(self, item_id: str) -> None:
        queue = self.wavespeed_queue_widget.queue()
        item = next((value for value in queue.items if value.item_id == item_id), None)
        if item is None or item.status == QueueStatus.RUNNING:
            return
        item.retry()
        queue.paused = False
        self._persist_wavespeed_campaign_queue(queue)
        self.wavespeed_queue_widget.set_item_update(item)
        if not self._wavespeed_queue_running:
            self._start_wavespeed_queue()

    def _retry_wavespeed_failed(self) -> None:
        queue = self.wavespeed_queue_widget.queue()
        for item in queue.items:
            if item.status == QueueStatus.FAILED:
                item.retry()
        queue.paused = False
        self._persist_wavespeed_campaign_queue(queue)
        for item in queue.items:
            self.wavespeed_queue_widget.set_item_update(item)
        if not self._wavespeed_queue_running:
            self._start_wavespeed_queue()

    def _clear_wavespeed_completed(self) -> None:
        queue = self.wavespeed_queue_widget.queue()
        queue.items = [item for item in queue.items if item.status != QueueStatus.COMPLETED]
        self.wavespeed_queue_widget.load_queue(queue)
        self._persist_wavespeed_campaign_queue(queue)

    def _wavespeed_estimate_price(self) -> None:
        snapshot = self._wavespeed_snapshot()
        self._wavespeed_start_busy("Subiendo referencias para estimar precio...")

        def task():
            payload, _uploaded = self._wavespeed_upload_and_build(snapshot)
            return self.wavespeed.estimate_price(payload, model_id=snapshot["model_id"])

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
                item.get("model_id", WAN_MODEL_ID),
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
        elif kind == "face_swap_generate":
            self.face_swap_generate_button.setEnabled(True)
            if isinstance(payload, Exception):
                self.face_swap_status_label.setText(f"Error: {payload}")
            else:
                prediction = payload["prediction"]
                self.face_swap_raw_edit.setPlainText(json.dumps(prediction.raw, ensure_ascii=False, indent=2))
                message = f"{prediction.id}: {prediction.status}"
                if payload.get("output_file"):
                    message += f" · guardado en {payload['output_file']}"
                self.face_swap_status_label.setText(message)
                self._wavespeed_refresh_history()
        elif kind == "flux_price":
            if isinstance(payload, Exception):
                self.flux_price_label.setText(f"Precio: error: {payload}")
            else:
                self.flux_price_label.setText(f"Precio estimado: ${payload.get('discounted_price', payload.get('price', '?'))} USD")
        elif kind == "flux_generate":
            self.flux_generate_button.setEnabled(True)
            if isinstance(payload, Exception):
                self.flux_status_label.setText(f"Error: {payload}")
            else:
                prediction = payload["prediction"]
                self.flux_task_edit.setText(prediction.id)
                self.flux_raw_edit.setPlainText(json.dumps(prediction.raw, ensure_ascii=False, indent=2))
                self.flux_status_label.setText(f"{prediction.id}: {prediction.status} · {payload['output_file']}")
                self._wavespeed_refresh_history()
        elif kind == "flux_query":
            if isinstance(payload, Exception):
                self.flux_status_label.setText(f"Error: {payload}")
            else:
                self.flux_raw_edit.setPlainText(json.dumps(payload.raw, ensure_ascii=False, indent=2))
                self.flux_status_label.setText(f"{payload.id}: {payload.status}")
        elif kind == "flux_queue_item":
            self.flux_queue_widget.set_item_update(payload)
            if payload.status in {QueueStatus.COMPLETED, QueueStatus.FAILED}:
                self._wavespeed_refresh_history()
        elif kind == "flux_queue_prices":
            self.flux_queue_widget.estimate_button.setEnabled(True)
            if isinstance(payload, Exception):
                self.flux_queue_widget.set_total_price(None, str(payload))
            else:
                self.flux_queue_widget.set_total_price(float(payload))
        elif kind == "flux_queue_finished":
            self._flux_queue_running = False
            self._flux_queue_active = None
            self.flux_queue_widget.set_running(False)
            self.flux_queue_add_button.setEnabled(True)
            self.flux_queue_widget.status_label.setText(
                f"Error: {payload}" if isinstance(payload, Exception) else "Cola Flux finalizada.")
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
        elif kind == "queue_item":
            self.wavespeed_queue_widget.set_item_update(payload)
        elif kind == "queue_prices":
            self.wavespeed_queue_estimate_button.setEnabled(True)
            self.wavespeed_queue_widget.set_estimating(False)
            if isinstance(payload, Exception):
                self.wavespeed_queue_total_label.setText(f"Total cola: error: {payload}")
                self.wavespeed_queue_widget.set_total_price(None, str(payload))
                self.wavespeed_queue_widget.set_error(str(payload))
            else:
                self.wavespeed_queue_total_label.setText(f"Total cola: ${float(payload):.4f} USD")
                self.wavespeed_queue_widget.set_total_price(float(payload))
                self.wavespeed_queue_widget.set_error("Costos estimados.")
        elif kind == "queue_finished":
            self._wavespeed_queue_running = False
            self._wavespeed_queue_active = None
            self.wavespeed_queue_widget.set_running(False)
            if isinstance(payload, Exception):
                self.wavespeed_queue_widget.set_error(str(payload))
            else:
                self.wavespeed_queue_widget.set_error("Cola finalizada.")


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
