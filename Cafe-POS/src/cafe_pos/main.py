from __future__ import annotations

import sys

from PySide6.QtCore import QLocale, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .db import Database
from .diagnostics import install_crash_diagnostics
from .i18n import qt_locale, set_language
from .paths import get_asset_path, get_database_path
from .theme import apply_theme
from .ui.animations import set_reduced_motion
from .ui.main_window import MainWindow


def main() -> int:
    install_crash_diagnostics()
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
    set_language(initial_cfg.app_language)
    QLocale.setDefault(qt_locale(initial_cfg.app_language))
    set_reduced_motion(initial_cfg.reduced_motion)
    apply_theme(
        app,
        initial_cfg,
        lang=initial_cfg.app_language,
        font_scale_pct=initial_cfg.default_font_scale_pct,
        density=initial_cfg.default_ui_density,
    )

    window = MainWindow(db, app)
    window.apply_launch_window_state()

    if not db.has_admin():
        if not window.ensure_initial_admin_ready():
            window.close()
            return 0

    window.check_and_run_due_auto_backup()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
