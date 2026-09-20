from __future__ import annotations

import sys

from app.database.db import Database
from app.database.repositories import CampaignRepository
from app.sogni.auth import ApiKeyStore
from app.sogni.client import SogniClient
from app.ui.main_window import MainWindow
from app.utils.logging import configure_logging
from app.utils.paths import default_db_path


def main() -> int:
    configure_logging()
    db = Database(default_db_path())
    db.initialize()
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 is not installed. Run: py -m pip install -r requirements.txt")
        return 1
    app = QApplication(sys.argv)
    key_store = ApiKeyStore()
    window = MainWindow(CampaignRepository(db), SogniClient(key_store.get()), key_store)
    window.resize(1180, 760)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
