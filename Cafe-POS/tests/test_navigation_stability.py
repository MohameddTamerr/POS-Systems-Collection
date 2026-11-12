from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, qInstallMessageHandler, qWarning
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid as _qt_is_valid

import cafe_pos.diagnostics as diag_mod
from cafe_pos.config import BusinessConfig
from cafe_pos.db import Database
from cafe_pos.diagnostics import install_crash_diagnostics
from cafe_pos.icons import render_category_corner_artwork
from cafe_pos.paths import get_database_path
from cafe_pos.ui.main_window import MainWindow
from cafe_pos.ui.widgets import BoutiqueCategoryCard, CozyProductCard


def _pump_events(qapp: QApplication, duration_ms: int = 20) -> None:
    deadline = time.monotonic() + max(0.0, duration_ms / 1000.0)
    qapp.processEvents()
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)


def _seed_store(lang: str) -> tuple[Database, object]:
    db = Database(get_database_path())
    cfg = BusinessConfig(
        business_name="MoCoffee Boutique",
        accent_color="#005B37",
        currency="EGP" if lang == "ar-EG" else "USD",
        app_language=lang,
        setup_completed=True,
        reduced_motion=False,
    )
    _saved_cfg, admin = db.initialize_owner_and_business(
        business_cfg=cfg,
        admin_username="admin_owner",
        admin_password="AdminStrong!2026",
        admin_display_name="مدير المتجر" if lang == "ar-EG" else "Store Admin",
    )
    fresh_admin = db.update_user(admin.id, preferred_language=lang, actor=admin)
    return db, fresh_admin


def _assert_register_intact(win: MainWindow, *, expected_min_cards: int = 1) -> None:
    reg = win.register_view
    assert win.stack.currentIndex() == 0

    cat_cards: list[BoutiqueCategoryCard] = []
    for i in range(reg.category_layout.count()):
        item = reg.category_layout.itemAt(i)
        w = item.widget() if item is not None else None
        if isinstance(w, BoutiqueCategoryCard):
            cat_cards.append(w)
    assert len(cat_cards) >= 2
    for cc in cat_cards:
        assert _qt_is_valid(cc)
        assert cc.isVisible()
        assert cc.width() > 40 and cc.height() > 40
        assert cc.title_lbl.text().strip() != ""
        assert cc.count_lbl.text().strip() != ""
        cc.repaint()

    assert len(reg._product_cards) >= expected_min_cards
    for card in reg._product_cards.values():
        assert isinstance(card, CozyProductCard)
        assert _qt_is_valid(card)
        assert card.isVisible()
        assert card.width() > 60 and card.height() > 60
        assert card.name_lbl.text().strip() != ""
        assert card.price_lbl.text().strip() != ""
        pm = card.photo_lbl.pixmap()
        assert pm is not None and not pm.isNull()
        card.repaint()
        card.qty_control.repaint()


def test_corner_artwork_handles_espresso_and_custom_categories(qapp: QApplication) -> None:
    """Every seeded or custom category (including Espresso/Other) must paint cleanly."""
    for cat in ("All", "Espresso", "Cold Drinks", "Tea & Matcha", "Bakery", "Retail", "Other", "Seasonal"):
        pm = render_category_corner_artwork(cat, "#C5CEC0", 68, 52)
        assert pm is not None and not pm.isNull()


def test_crash_diagnostics_logs_warnings_without_disabling_qt(
    isolated_data_dir: Path, qapp: QApplication
) -> None:
    """Crash diagnostics writes warnings to temp data_dir logs without crashing Qt."""
    prev_handler = qInstallMessageHandler(None)
    qInstallMessageHandler(prev_handler)
    diag_mod._INITIALIZED = False
    logger = logging.getLogger("cafe_pos.diagnostics")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    try:
        log_path = install_crash_diagnostics()
        assert log_path is not None
        assert log_path.parent == isolated_data_dir / "logs"
        qWarning("diag_smoke_check")
        for h in logger.handlers:
            h.flush()
        assert log_path.exists()
        assert "diag_smoke_check" in log_path.read_text(encoding="utf-8")
    finally:
        qInstallMessageHandler(prev_handler)
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()
        if diag_mod._FAULT_FILE is not None:
            try:
                import faulthandler

                faulthandler.disable()
                diag_mod._FAULT_FILE.close()
            except Exception:
                pass
            diag_mod._FAULT_FILE = None
        diag_mod._INITIALIZED = False


def test_arabic_rtl_repeated_navigation_and_live_language_toggle(
    isolated_data_dir: Path, qapp: QApplication
) -> None:
    """Reproduce Arabic RTL multi-page navigation (>=55 transitions) with reduced_motion=False."""
    qt_warnings: list[str] = []
    uncaught_excs: list[BaseException] = []

    def _msg_handler(_mode, _ctx, msg: str) -> None:
        if any(
            token in msg
            for token in (
                "QPainter",
                "Painter not active",
                "QWidgetEffectSourcePrivate",
                "QPaintDevice",
                "Cannot destroy",
                "NameError",
            )
        ):
            qt_warnings.append(msg)

    prev_handler = qInstallMessageHandler(_msg_handler)
    prev_hook = sys.excepthook

    def _exc_hook(exc_type, exc_val, exc_tb) -> None:
        uncaught_excs.append(exc_val)

    sys.excepthook = _exc_hook

    db, admin = _seed_store("ar-EG")
    win = MainWindow(db, qapp)
    try:
        win.resize(1280, 800)
        win.show()
        win.login_user(admin)
        _pump_events(qapp, 80)

        assert qapp.layoutDirection() == Qt.RightToLeft
        _assert_register_intact(win, expected_min_cards=10)

        # Add item to cart and adjust qty to verify cart state + stepper widgets survive navigation
        first_pid = next(iter(win.register_view._product_cards.keys()))
        first_card = win.register_view._product_cards[first_pid]
        first_card.qty_control.click_plus()
        _pump_events(qapp, 40)
        assert win.register_view.cart[first_pid].quantity == 1

        # Cycle across all Admin destinations (Products/Inventory, Sales History, Reports, Users, Settings -> Register)
        route = [1, 0, 2, 0, 3, 0, 4, 0, 5, 0]
        transitions = 0
        for cycle in range(6):
            for idx in route:
                win.switch_view(idx)
                transitions += 1
                if idx == 1:
                    win.products_view.switch_subview(1)  # Inventory subview
                    _pump_events(qapp, 18)
                    win.products_view.switch_subview(0)
                if cycle % 2 == 1:
                    # Resize stress during animation/transition
                    win.resize(960, 600) if transitions % 2 else win.resize(1280, 800)
                _pump_events(qapp, 22 if cycle < 4 else 12)
                if idx == 0:
                    _assert_register_intact(win, expected_min_cards=10)

        assert transitions == 60
        assert win.register_view.cart[first_pid].quantity == 1

        # Clicking product card after 60 navigations must add exactly +1 (no duplicated signals)
        win.register_view._product_cards[first_pid].qty_control.click_plus()
        _pump_events(qapp, 30)
        assert win.register_view.cart[first_pid].quantity == 2

        # Preserve active search filter + category filter + cart across navigation & live EN <-> AR toggle
        win.register_view.search_input.setText("Latte")
        _pump_events(qapp, 25)
        filtered_count = len(win.register_view._product_cards)
        assert filtered_count >= 1
        win.switch_view(3)
        _pump_events(qapp, 25)
        win.switch_language("en", persist_store=False)
        _pump_events(qapp, 35)
        assert qapp.layoutDirection() == Qt.LeftToRight
        win.switch_view(0)
        _pump_events(qapp, 60)
        assert win.register_view.search_input.text() == "Latte"
        assert win.register_view.cart[first_pid].quantity == 2
        _assert_register_intact(win, expected_min_cards=filtered_count)

        # Toggle back to Arabic RTL while on Register and switch away/back again
        win.switch_language("ar-EG", persist_store=False)
        _pump_events(qapp, 40)
        assert qapp.layoutDirection() == Qt.RightToLeft
        win.switch_view(5)
        _pump_events(qapp, 30)
        win.switch_view(0)
        _pump_events(qapp, 60)
        _assert_register_intact(win, expected_min_cards=filtered_count)

        assert qt_warnings == [], f"Unexpected Qt painter/lifecycle warnings: {qt_warnings}"
        assert uncaught_excs == [], f"Uncaught exceptions during paint/nav: {uncaught_excs}"
    finally:
        win.close()
        _pump_events(qapp, 25)
        qInstallMessageHandler(prev_handler)
        sys.excepthook = prev_hook


def test_english_ltr_rapid_navigation_preserves_state_and_no_warnings(
    isolated_data_dir: Path, qapp: QApplication
) -> None:
    """Verify rapid (<15ms) English LTR transitions + category filter + resize do not drop cards or warn."""
    qt_warnings: list[str] = []
    uncaught_excs: list[BaseException] = []

    def _msg_handler(_mode, _ctx, msg: str) -> None:
        if any(
            token in msg
            for token in (
                "QPainter",
                "Painter not active",
                "QWidgetEffectSourcePrivate",
                "QPaintDevice",
                "Cannot destroy",
            )
        ):
            qt_warnings.append(msg)

    prev_handler = qInstallMessageHandler(_msg_handler)
    prev_hook = sys.excepthook

    def _exc_hook(exc_type, exc_val, exc_tb) -> None:
        uncaught_excs.append(exc_val)

    sys.excepthook = _exc_hook

    db, admin = _seed_store("en")
    win = MainWindow(db, qapp)
    try:
        win.resize(1240, 780)
        win.show()
        win.login_user(admin)
        _pump_events(qapp, 60)
        assert qapp.layoutDirection() == Qt.LeftToRight

        # Filter to Espresso specifically (the exact category artwork previously raising NameError)
        win.register_view._select_category("Espresso")
        _pump_events(qapp, 35)
        espresso_count = len(win.register_view._product_cards)
        assert espresso_count >= 1
        _assert_register_intact(win, expected_min_cards=espresso_count)

        # Rapid 52-transition burst (interrupting cross-fades mid-flight)
        burst = [1, 2, 0, 3, 5, 0, 4, 0, 1, 0, 2, 5, 0] * 4
        for step_idx, view_idx in enumerate(burst):
            win.switch_view(view_idx)
            if step_idx % 5 == 0:
                QTimer.singleShot(0, lambda: None)
            _pump_events(qapp, 10)
        assert len(burst) == 52

        win.switch_view(0)
        _pump_events(qapp, 80)
        assert win.register_view.selected_category == "Espresso"
        _assert_register_intact(win, expected_min_cards=espresso_count)
        assert qt_warnings == [], f"Unexpected Qt painter/lifecycle warnings: {qt_warnings}"
        assert uncaught_excs == [], f"Uncaught exceptions during paint/nav: {uncaught_excs}"
    finally:
        win.close()
        _pump_events(qapp, 25)
        qInstallMessageHandler(prev_handler)
        sys.excepthook = prev_hook
