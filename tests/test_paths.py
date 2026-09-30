from pathlib import Path

from app.utils import paths


def test_frozen_app_uses_persistent_user_data_directory(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    result = paths.data_dir()

    assert result == tmp_path / "LocalAppData" / paths.APP_NAME / "data"
    assert result.is_dir()
    assert paths.default_db_path() == result / "app.db"


def test_source_app_keeps_data_in_project_directory(monkeypatch):
    monkeypatch.delattr(paths.sys, "frozen", raising=False)

    assert paths.data_dir() == paths.project_root() / "data"
