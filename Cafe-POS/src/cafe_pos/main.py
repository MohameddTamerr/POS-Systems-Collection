from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .db import Database
from .paths import get_asset_path, get_database_path
from .theme import apply_theme
from .ui.main_window import MainWindow


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("CafePOS")
    app.setOrganizationName("MohamedTamer")

    icon_path = get_asset_path("app_icon.ico")
    if not icon_path.exists():
        icon_path = get_asset_path("app_icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    db = Database(get_database_path())
    initial_cfg = db.get_settings()
    apply_theme(app, initial_cfg)

    window = MainWindow(db, app)
    window.show()

    if not initial_cfg.setup_completed:
        window.run_setup_wizard(is_first_launch=True)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

