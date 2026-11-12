from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure src/ is importable during test execution
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "appdata" / "CafePOS"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CAFE_POS_DATA_DIR", str(data_dir))
    return data_dir


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _reset_language_and_cleanup_qt():
    from PySide6.QtCore import QLocale, Qt
    from PySide6.QtWidgets import QApplication

    from cafe_pos.config import BusinessConfig
    from cafe_pos.i18n import qt_locale, set_language
    from cafe_pos.theme import apply_theme

    def _reset_env() -> None:
        set_language("en")
        QLocale.setDefault(qt_locale("en"))
        app = QApplication.instance()
        if app is not None:
            app.setLayoutDirection(Qt.LeftToRight)
            apply_theme(app, BusinessConfig(), lang="en")

    _reset_env()
    yield

    app = QApplication.instance()
    if app is not None:
        for w in list(app.topLevelWidgets()):
            try:
                w.close()
                w.deleteLater()
            except RuntimeError:
                pass
        app.processEvents()
    _reset_env()

