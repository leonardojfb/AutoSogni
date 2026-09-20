from __future__ import annotations

import threading
from pathlib import Path

from app.core.campaign_manager import CampaignManager
from app.core.model_settings import ASPECT_RATIOS, build_campaign_settings
from app.core.queue_manager import QueueManager
from app.core.job_runner import JobRunner
from app.database.repositories import CampaignRepository
from app.sogni.schemas import ModelDescriptor

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
        QMainWindow,
        QMessageBox,
        QPushButton,
        QProgressBar,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    Qt = None
    QPixmap = None
    QMainWindow = object


class MainWindow(QMainWindow):
    def __init__(self, repo: CampaignRepository, sogni_client, key_store) -> None:
        super().__init__()
        self.repo = repo
        self.sogni = sogni_client
        self.key_store = key_store
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


if Qt is not None:
    from PySide6.QtCore import QEvent

    class _RefreshEvent(QEvent):
        TYPE = QEvent.Type(QEvent.registerEventType())

        def __init__(self) -> None:
            super().__init__(self.TYPE)

    def customEvent(self, event):
        if event.type() == _RefreshEvent.TYPE:
            self._queue_running = False
            self.start_button.setEnabled(True)
            self._load_campaigns()

    MainWindow.customEvent = customEvent


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
