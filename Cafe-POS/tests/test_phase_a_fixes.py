from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from cafe_pos.auth import ROLE_CASHIER, UserAccount
from cafe_pos.config import BusinessConfig
from cafe_pos.db import CartLine, Database
from cafe_pos.i18n import get_language, set_language, tr
from cafe_pos.receipts import build_receipt_document, build_receipt_plain_text
from cafe_pos.theme import build_stylesheet
from cafe_pos.ui.display_preferences_dialog import DisplayPreferencesDialog
from cafe_pos.ui.main_window import MainWindow
from cafe_pos.ui.product_editor_dialog import ProductEditorDialog


def _seed_db(isolated_data_dir: Path) -> tuple[Database, UserAccount, UserAccount]:
    db = Database(isolated_data_dir / "cafe_pos.db")
    cfg = BusinessConfig(
        business_name="MoCoffee",
        address="12 Nile St",
        phone="+20 100 000 0000",
        currency="EGP",
        app_language="en",
        setup_completed=True,
    )
    _, admin = db.initialize_owner_and_business(
        business_cfg=cfg,
        admin_username="owner",
        admin_password="Password123!",
        admin_display_name="Owner Admin",
    )
    db.set_session_actor(admin)
    cashier = db.create_user(
        "sara_cashier",
        "CashierPass123!",
        role=ROLE_CASHIER,
        display_name="Sara M",
    )
    return db, admin, cashier


def test_category_creation_in_combo_no_freeze_or_loop(isolated_data_dir, qapp) -> None:
    db, admin, _ = _seed_db(isolated_data_dir)
    db.set_session_actor(admin)
    cfg = db.get_settings()

    dlg = ProductEditorDialog(cfg, db.list_categories(), db=db, is_admin=True)
    dlg.show()
    qapp.processEvents()

    combo = dlg.category_combo
    assert combo.completer() is None

    # Rapid typing & showing popup must stay bounded with no re-entrant storm
    combo.showPopup()
    qapp.processEvents()
    for partial in ["S", "Si", "Sig", "Sign", "Signature Blends"]:
        combo.setEditText(partial)
        qapp.processEvents()

    combo.stage_pending_category("Signature Blends")
    qapp.processEvents()
    assert combo.currentText().strip() == "Signature Blends"
    assert combo.resolve_selection().is_pending_new

    # Saving should prepare pending category + hide popup cleanly without blocking
    dlg.name_edit.setText("Mocha Royale")
    dlg.price_edit.setText("85.00")
    qapp.processEvents()
    dlg._on_save()
    qapp.processEvents()

    assert dlg.result() == QDialog.Accepted
    assert dlg.result_pending_cat_en == "Signature Blends"
    assert dlg.result_category == "Signature Blends"
    assert len(combo._diag_events) < 80
    dlg.close()


def test_fullscreen_and_window_state_persistence(isolated_data_dir, qapp) -> None:
    db, admin, _ = _seed_db(isolated_data_dir)
    cfg = db.get_settings()
    assert cfg.window_state_pref == "maximized"

    win = MainWindow(db, qapp)
    win.apply_launch_window_state()
    qapp.processEvents()
    win.login_user(admin)
    qapp.processEvents()

    # Toggle F11 fullscreen and verify Escape exits without trapping state
    win.toggle_fullscreen()
    qapp.processEvents()
    assert win.isFullScreen()

    win._on_escape_pressed()
    qapp.processEvents()
    assert not win.isFullScreen()

    # Even if closed while fullscreen, persistent pref must only ever save maximized or normal
    win.toggle_fullscreen()
    qapp.processEvents()
    win.close()
    qapp.processEvents()

    saved_cfg = db.get_settings()
    assert saved_cfg.window_state_pref in ("maximized", "normal")

    # Dialog clamping inside screen bounds
    win2 = MainWindow(db, qapp)
    win2.login_user(admin)
    dlg = QDialog(win2)
    dlg.resize(5000, 4000)
    win2.clamp_dialog_to_screen(dlg)
    assert dlg.width() <= 3840
    assert dlg.height() <= 2160
    dlg.close()
    win2.close()


def test_per_user_language_preference_and_live_cart_preservation(isolated_data_dir, qapp) -> None:
    db, admin, cashier = _seed_db(isolated_data_dir)
    win = MainWindow(db, qapp)
    win.login_user(cashier)
    qapp.processEvents()

    # Cashier adds an item before switching language
    products = db.list_products()
    assert products
    win.register_view.add_product_to_cart(products[0])
    qapp.processEvents()
    assert len(win.register_view.cart) == 1
    first_price = win.register_view.cart[list(win.register_view.cart.keys())[0]].unit_price_minor

    # Cashier switches to Arabic via Profile menu handler
    win._on_user_select_language("ar-EG")
    qapp.processEvents()
    assert get_language() == "ar-EG"
    assert qapp.layoutDirection() == Qt.RightToLeft
    assert win.current_user is not None
    assert win.current_user.preferred_language == "ar-EG"
    # Store default stays English while Cashier's preference is Arabic
    assert db.get_settings().app_language == "en"
    # Cart intact!
    assert len(win.register_view.cart) == 1
    assert win.register_view.cart[list(win.register_view.cart.keys())[0]].unit_price_minor == first_price

    # Logout restores store language; Admin logs in with English; Cashier re-login restores Arabic
    win.logout(reason="manual")
    qapp.processEvents()
    assert get_language() == "en"

    fresh_admin = db.authenticate("owner", "Password123!")
    win.login_user(fresh_admin)
    qapp.processEvents()
    assert get_language() == "en"
    win.logout(reason="manual")

    fresh_cashier = db.authenticate("sara_cashier", "CashierPass123!")
    win.login_user(fresh_cashier)
    qapp.processEvents()
    assert get_language() == "ar-EG"
    assert qapp.layoutDirection() == Qt.RightToLeft
    win.close()


def test_per_user_font_scale_and_density_self_service(isolated_data_dir, qapp) -> None:
    db, admin, cashier = _seed_db(isolated_data_dir)
    cfg = db.get_settings()

    win = MainWindow(db, qapp)
    win.login_user(cashier)
    qapp.processEvents()

    dlg = DisplayPreferencesDialog(db, win.current_user, cfg, parent=win)
    dlg._set_scale(130)
    d_idx = dlg.density_combo.findData("compact")
    dlg.density_combo.setCurrentIndex(d_idx)
    dlg._on_save()
    qapp.processEvents()

    assert dlg.updated_user is not None
    assert dlg.updated_user.font_scale_pct == 130
    assert dlg.updated_user.ui_density == "compact"

    # Stylesheet respects 130% scaling & header height grows responsively
    qss = build_stylesheet(cfg.accent_color, font_scale_pct=130, density="compact")
    assert "16.9px" in qss

    # Restore defaults resets to 100% and comfortable
    dlg2 = DisplayPreferencesDialog(db, dlg.updated_user, cfg, parent=win)
    dlg2._restore_defaults()
    assert dlg2._scale_pct == 100
    assert dlg2._density == "comfortable"
    dlg2._on_save()
    assert dlg2.updated_user is not None
    assert dlg2.updated_user.font_scale_pct == 100

    # Save 115% and verify persistence across re-authentication, and logout resets UI scale
    dlg3 = DisplayPreferencesDialog(db, dlg2.updated_user, cfg, parent=win)
    dlg3._set_scale(115)
    dlg3._on_save()
    win.logout(reason="manual")
    assert win._active_display_prefs()[0] == 100

    fresh_cashier = db.authenticate("sara_cashier", "CashierPass123!")
    assert fresh_cashier.font_scale_pct == 115
    win.close()


def test_cashier_info_on_receipts_and_legacy_fallback(isolated_data_dir, qapp) -> None:
    db, admin, cashier = _seed_db(isolated_data_dir)
    db.set_session_actor(cashier)
    products = db.list_products()
    p = products[0]
    line = CartLine(
        product_id=p.id,
        name=p.name,
        unit_price_minor=p.price_minor,
        quantity=1,
        name_en=p.name_en,
        name_ar=p.name_ar,
        base_unit_price_minor=p.price_minor,
    )
    order = db.create_order(
        [line],
        cash_received_minor=p.price_minor,
        actor=cashier,
    )
    assert order.cashier_user_id == cashier.id
    assert order.cashier_name_snapshot == "Sara M"

    cfg = db.get_settings()
    en_txt = build_receipt_plain_text(order, cfg, lang="en")
    assert "Sara M" in en_txt
    ar_txt = build_receipt_plain_text(order, cfg, lang="ar-EG")
    assert "Sara M" in ar_txt
    ar_doc = build_receipt_document(order, cfg, lang="ar-EG")
    assert "Sara M" in ar_doc.toPlainText()
    assert "Sara M" in ar_doc.toHtml().replace("\xa0", " ")

    # Toggle show_cashier_on_receipt=False hides cashier line cleanly
    cfg_hidden = db.get_settings()
    cfg_hidden.show_cashier_on_receipt = False
    assert "Sara M" not in build_receipt_plain_text(order, cfg_hidden, lang="en")

    # Legacy row with empty cashier snapshot displays neutral Unspecified / غير محدد without mutating DB
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE orders SET cashier_user_id = NULL, cashier_name_snapshot = '' WHERE id = ?",
            (order.id,),
        )
        conn.commit()
    legacy = db.get_order(order.id, actor=admin)
    assert legacy.cashier_name_snapshot == ""
    assert tr("receipt.cashier_unspecified", lang="en") in build_receipt_plain_text(legacy, cfg, lang="en")
    assert tr("receipt.cashier_unspecified", lang="ar-EG") in build_receipt_plain_text(legacy, cfg, lang="ar-EG")
    assert db.get_order(order.id, actor=admin).cashier_name_snapshot == ""


def test_translation_key_parity_en_ar() -> None:
    root = Path(__file__).resolve().parent.parent / "src" / "cafe_pos" / "translations"
    en = json.loads((root / "en.json").read_text(encoding="utf-8"))
    ar = json.loads((root / "ar_EG.json").read_text(encoding="utf-8"))
    assert set(en.keys()) == set(ar.keys())
