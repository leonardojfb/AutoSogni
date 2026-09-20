from __future__ import annotations

from pathlib import Path


APP_NAME = "SogniVideoAutomator"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    path = project_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = project_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_db_path() -> Path:
    return data_dir() / "app.db"
