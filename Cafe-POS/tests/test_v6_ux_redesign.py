from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog

from cafe_pos.auth import ROLE_CASHIER
from cafe_pos.config import BusinessConfig
from cafe_pos.db import CURRENT_SCHEMA_VERSION, CartLine, Database
from cafe_pos.i18n import get_language
from cafe_pos.paths import get_database_path
from cafe_pos.payments import (
    METHOD_APPLE_PAY,
    METHOD_CARD,
    METHOD_CASH,
    METHOD_EWALLET,
    METHOD_INSTAPAY,
    PaymentSettingsConfig,
    STATUS_CASH_SETTLED,
    STATUS_CONFIRMED_EXTERNAL,
    default_payment_settings,
    serialize_payment_settings,
)
from cafe_pos.ui.main_window import MainWindow
from cafe_pos.ui.payment_dialog import CheckoutPaymentDialog


def _seed_store(db: Database, *, country: str = "EG", currency: str = "EGP", country_configured: bool = True):
    cfg = BusinessConfig(
        business_name="Nile Blossom Café",
        accent_color="#005B37",
        currency=currency,
        business_country=country,
        country_configured=country_configured,
        setup_completed=True,
    )
    saved_cfg, admin = db.initialize_owner_and_business(
        business_cfg=cfg,
        admin_username="owner",
        admin_password="OwnerStrongPassword!1",
        admin_display_name="Omar Admin",
    )
    cashier = db.create_user(
        "sara",
        "CashierStrong!2026",
        ROLE_CASHIER,
        display_name="Sara Ahmed",
        actor=admin,
    )
    return saved_cfg, admin, cashier


def test_login_two_column_responsive_whitelabel_and_rtl(qapp, isolated_data_dir: Path):
    db = Database(get_database_path())
    saved_cfg, _admin, _cashier = _seed_store(db)

    window = MainWindow(db, qapp)
    try:
        window.resize(1260, 780)
        window.show()
        qapp.processEvents()

        lv = window.login_view
        assert window.root_stack.currentIndex() == 0
        assert not lv._raw_hero_pixmap.isNull()
        assert lv.hero_panel.isVisible() is True
        assert lv.form_panel.isVisible() is True
        assert lv.store_title_lbl.text() == "Nile Blossom Café"
        assert lv.welcome_heading_lbl.text() == "Welcome Back"
        assert lv.hero_pill_lbl.text() == "Offline Desktop POS"

        # Responsive width collapse (< 780) hides hero artwork cleanly
        lv.resize(740, 700)
        lv._update_responsive_split()
        assert lv.hero_panel.isVisible() is False
        assert lv.form_panel.isVisible() is True

        # Responsive height collapse (< 500) hides hero artwork cleanly
        lv.resize(1100, 485)
        lv._update_responsive_split()
        assert lv.hero_panel.isVisible() is False

        # Restores two-column split at desktop size
        lv.resize(1200, 740)
        lv._update_responsive_split()
        assert lv.hero_panel.isVisible() is True

        # Live Arabic RTL switch on login screen updates all hero & form labels
        lv.lang_selector._on_pick("ar-EG")
        qapp.processEvents()
        assert get_language() == "ar-EG"
        assert window.layoutDirection() == Qt.RightToLeft
        assert lv.welcome_heading_lbl.text() == "مرحبًا بعودتك"
        assert lv.hero_pill_lbl.text() == "نقطة بيع مكتبية محلية"

        # Switch back to EN & sign in via form
        lv.lang_selector._on_pick("en")
        qapp.processEvents()
        lv.username_edit.setText("sara")
        lv.password_edit.setText("CashierStrong!2026")
        lv.sign_in_btn.click()
        assert window.is_authenticated is True
        assert window.current_user.username == "sara"
    finally:
        window.close()


def test_checkout_cash_exact_and_increment_quick_buttons_and_cancel_preserves_cart(
    qapp, isolated_data_dir: Path, monkeypatch
):
    db = Database(get_database_path())
    saved_cfg, admin, _cashier = _seed_store(db, country="EG", currency="EGP")
    monkeypatch.setattr("cafe_pos.ui.main_window.ReceiptDialog.exec", lambda self: 1)

    window = MainWindow(db, qapp)
    try:
        window.login_user(admin)
        reg = window.register_view
        assert not hasattr(reg, "cash_input")
        assert not hasattr(reg, "change_val_lbl")

        prod = next(p for p in db.list_products() if p.name == "Butter Croissant")
        # Normalize price to 35.00 EGP (3500 minor) for crisp arithmetic checks
        prod = db.update_product(prod.id, prod.name, prod.category, 3500, actor=admin)
        reg.refresh_catalog()
        reg.add_product_to_cart(prod)
        assert reg.checkout_btn.isEnabled() is True
        assert "35" in reg.checkout_btn.text()

        # Cancel modal keeps cart untouched
        def _cancel_modal(attempt: int = 0) -> None:
            dlg = qapp.activeModalWidget()
            if isinstance(dlg, CheckoutPaymentDialog):
                dlg.cancel_btn.click()
            elif attempt < 30:
                QTimer.singleShot(10, lambda: _cancel_modal(attempt + 1))

        QTimer.singleShot(0, _cancel_modal)
        reg.trigger_checkout()
        assert len(reg.cart) == 1
        assert db.get_order_count() == 0

        # Direct CheckoutPaymentDialog verification for Exact, +10, +20, +50
        dlg = CheckoutPaymentDialog(db, saved_cfg, list(reg.cart.values()))
        try:
            assert sorted(dlg.quick_add_buttons.keys()) == [10, 20, 50]
            assert dlg.cash_exact_btn is not None
            assert dlg.confirm_btn.isEnabled() is False

            # +10 from empty sets 10.00 (still insufficient for 35.00)
            dlg.quick_add_buttons[10].click()
            assert dlg.cash_edit.text() == "10.00"
            assert dlg.confirm_btn.isEnabled() is False

            # +20 accumulates to 30.00 (still insufficient)
            dlg.quick_add_buttons[20].click()
            assert dlg.cash_edit.text() == "30.00"
            assert dlg.confirm_btn.isEnabled() is False

            # +50 accumulates to 80.00 (change 45.00, valid!)
            dlg.quick_add_buttons[50].click()
            assert dlg.cash_edit.text() == "80.00"
            assert dlg.confirm_btn.isEnabled() is True
            assert "45.00" in dlg.cash_change_val_lbl.text()

            # Exact resets directly to exact total 35.00 (change 0.00)
            dlg.cash_exact_btn.click()
            assert dlg.cash_edit.text() == "35.00"
            assert dlg.confirm_btn.isEnabled() is True
            assert "0.00" in dlg.cash_change_val_lbl.text()

            # Double-submit guard only creates 1 order
            dlg._on_confirm()
            dlg._on_confirm()
            assert dlg.result() == QDialog.Accepted
            assert db.get_order_count() == 1
        finally:
            dlg.close()
    finally:
        window.close()


def test_egypt_apple_pay_instapay_info_and_wallet_toggles_and_country_banner(
    qapp, isolated_data_dir: Path, monkeypatch
):
    db = Database(get_database_path())
    # Start as US + EGP + country_configured=False (simulating legacy/auto EGP store)
    cfg, admin, cashier = _seed_store(db, country="US", currency="EGP", country_configured=False)

    window = MainWindow(db, qapp)
    try:
        window.login_user(admin)
        # Switching language to Arabic NEVER silently mutates business_country or enables InstaPay
        window.switch_language("ar-EG", persist_store=False)
        assert db.get_settings().business_country == "US"
        assert METHOD_INSTAPAY not in default_payment_settings("US").enabled_methods
        window.switch_language("en", persist_store=False)

        window.switch_view(5)
        sv = window.settings_view
        assert sv.egp_suggest_banner.isHidden() is False

        # Clicking "Configure Egypt (EG)" switches to EG with [CASH, APPLE_PAY, INSTAPAY, EWALLET]
        sv.egp_suggest_btn.click()
        assert sv.country_combo.currentData() == "EG"
        assert sv.egp_suggest_banner.isHidden() is True
        assert sv.chk_pay_cash.isChecked() is True
        assert sv.chk_pay_apple_pay.isChecked() is True
        assert sv.chk_pay_instapay.isChecked() is True
        assert sv.chk_pay_ewallet.isChecked() is True
        assert sv.chk_pay_card.isChecked() is False

        # Configure Store InstaPay Details & require Apple Pay ref & disable WE Pay
        sv.instapay_instr_edit.setText("nilecafe@instapay · 01001234567")
        sv.chk_req_apple_pay.setChecked(True)
        assert "WE Pay" in sv._wallet_toggle_checks
        assert "e& money" in sv._wallet_toggle_checks
        sv._wallet_toggle_checks["WE Pay"].setChecked(False)
        sv._save_settings()

        updated_cfg = db.get_settings()
        assert updated_cfg.business_country == "EG"
        assert updated_cfg.country_configured is True

        # Cannot disable ALL wallet providers when E-Wallet is enabled (validation guard)
        warnings: list[str] = []
        monkeypatch.setattr(
            "cafe_pos.ui.settings_view.QMessageBox.warning",
            lambda _parent, _title, msg: warnings.append(msg),
        )
        for chk in sv._wallet_toggle_checks.values():
            chk.setChecked(False)
        sv._save_settings()
        assert len(warnings) == 1
        assert updated_cfg.payment_methods_json == db.get_settings().payment_methods_json

        # Inspect CheckoutPaymentDialog for updated EG store
        db.set_session_actor(cashier)
        prod = db.list_products()[0]
        lines = [CartLine(product_id=prod.id, name=prod.name, unit_price_minor=4500, quantity=1)]
        dlg = CheckoutPaymentDialog(db, updated_cfg, lines)
        try:
            assert list(dlg.method_buttons.keys()) == [
                METHOD_CASH,
                METHOD_APPLE_PAY,
                METHOD_INSTAPAY,
                METHOD_EWALLET,
            ]
            assert METHOD_CARD not in dlg.method_buttons

            # Apple Pay enforces required reference & blocks PANs & needs checkbox
            assert dlg.select_method(METHOD_APPLE_PAY) is True
            dlg.apple_pay_confirm_chk.setChecked(True)
            assert dlg.confirm_btn.isEnabled() is False
            dlg.apple_pay_ref_edit.setText("4111111111111111")
            assert dlg.confirm_btn.isEnabled() is False
            dlg.apple_pay_ref_edit.setText("AP-9921")
            assert dlg.confirm_btn.isEnabled() is True

            # InstaPay displays merchant instructions & is standalone
            assert dlg.select_method(METHOD_INSTAPAY) is True
            assert dlg.instapay_info_box.isHidden() is False
            assert "nilecafe@instapay" in dlg.instapay_info_val_lbl.text()

            # E-Wallet contains e& money, excludes disabled WE Pay
            assert dlg.select_method(METHOD_EWALLET) is True
            wallet_items = [
                dlg.ewallet_provider_combo.itemText(i)
                for i in range(dlg.ewallet_provider_combo.count())
            ]
            assert "Vodafone Cash" in wallet_items
            assert "e& money" in wallet_items
            assert "WE Pay" not in wallet_items

            # Complete Apple Pay order cleanly
            assert dlg.select_method(METHOD_APPLE_PAY) is True
            dlg._on_confirm()
            assert dlg.result_order is not None
            assert dlg.result_order.primary_payment.payment_method == METHOD_APPLE_PAY
            assert dlg.result_order.primary_payment.payment_status == STATUS_CONFIRMED_EXTERNAL
        finally:
            dlg.close()

        # When instapay_instructions is empty, InstaPay info box stays hidden on checkout
        pm_no_instr = PaymentSettingsConfig(
            country="EG",
            customized=True,
            enabled_methods=[METHOD_CASH, METHOD_INSTAPAY],
            instapay_instructions="",
        )
        updated_cfg.payment_methods_json = serialize_payment_settings(pm_no_instr)
        dlg2 = CheckoutPaymentDialog(db, updated_cfg, lines)
        try:
            dlg2.select_method(METHOD_INSTAPAY)
            assert dlg2.instapay_info_box.isHidden() is True
        finally:
            dlg2.close()
    finally:
        window.close()


def test_top_nav_bar_profile_menu_and_responsive_overflow(qapp, isolated_data_dir: Path):
    db = Database(get_database_path())
    _cfg, admin, cashier = _seed_store(db)

    window = MainWindow(db, qapp)
    try:
        window.show()
        window.resize(1260, 780)
        window.login_user(admin)
        qapp.processEvents()

        # Top nav cleanup: no orders_chip, no standalone logout_btn, no session_pill
        assert not hasattr(window, "orders_chip")
        assert not hasattr(window, "logout_btn")
        assert not hasattr(window, "session_pill")
        assert window.register_view.orders_chip.isVisible() is True

        # Profile pill shows initials, display name, role badge, and Lock/Logout actions work
        assert window.profile_avatar_lbl.text() == "OA"
        assert "Omar Admin" in window.session_user_lbl.text()
        assert window.session_role_lbl.text() == "Admin"
        assert len(window.profile_menu.actions()) >= 3

        # Triggering Lock action from profile menu locks session cleanly
        window.menu_lock_action.trigger()
        assert window.is_authenticated is False
        assert window.root_stack.currentIndex() == 0

        # Sign in as Cashier -> only Register & Sales visible, no overflow needed at 1260
        window.login_user(cashier)
        assert window.profile_avatar_lbl.text() == "SA"
        assert window.nav_buttons[0].isVisibleTo(window.workspace_widget) is True
        assert window.nav_buttons[2].isVisibleTo(window.workspace_widget) is True
        assert window.nav_buttons[1].isVisibleTo(window.workspace_widget) is False
        assert window.nav_overflow_btn.isVisible() is False

        # Re-login as Admin and test responsive overflow at 980px width
        window.menu_logout_action.trigger()
        assert window.is_authenticated is False
        window.login_user(admin)
        window.resize(960, 720)
        qapp.processEvents()
        assert window.nav_overflow_btn.isVisible() is True
        overflow_texts = [act.text() for act in window.nav_overflow_menu.actions()]
        assert "Settings" in overflow_texts
        assert "Staff & Users" in overflow_texts
    finally:
        window.close()


def test_v5_to_v6_non_destructive_migration_allows_apple_pay_and_preserves_card(
    isolated_data_dir: Path,
):
    db_path = get_database_path()
    # Create a schema v5 database whose payments CHECK constraint only allows CASH/CARD/INSTAPAY/EWALLET/CUSTOM
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE business_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                business_name TEXT NOT NULL,
                accent_color TEXT NOT NULL,
                currency TEXT NOT NULL,
                address TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                logo_blob BLOB NOT NULL DEFAULT X'',
                logo_ext TEXT NOT NULL DEFAULT '',
                setup_completed INTEGER NOT NULL DEFAULT 1,
                next_order_seq INTEGER NOT NULL DEFAULT 1003,
                image_provider TEXT NOT NULL DEFAULT 'openverse',
                pexels_api_key TEXT NOT NULL DEFAULT '',
                reduced_motion INTEGER NOT NULL DEFAULT 0,
                auto_lock_minutes INTEGER NOT NULL DEFAULT 0,
                backup_enabled INTEGER NOT NULL DEFAULT 0,
                backup_frequency TEXT NOT NULL DEFAULT 'daily',
                backup_destination TEXT NOT NULL DEFAULT '',
                backup_retention_count INTEGER NOT NULL DEFAULT 7,
                last_auto_backup_at TEXT NOT NULL DEFAULT '',
                app_language TEXT NOT NULL DEFAULT 'en',
                receipt_language TEXT NOT NULL DEFAULT 'follow',
                business_country TEXT NOT NULL DEFAULT 'EG',
                payment_methods_json TEXT NOT NULL DEFAULT '',
                auto_save_receipt_pdf INTEGER NOT NULL DEFAULT 0,
                receipt_export_dir TEXT NOT NULL DEFAULT '',
                online_translation_enabled INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL DEFAULT '',
                preferred_language TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                actor_user_id INTEGER,
                actor_username TEXT NOT NULL DEFAULT '',
                actor_role TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                target_type TEXT NOT NULL DEFAULT '',
                target_id TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                normalized_key TEXT NOT NULL UNIQUE,
                name_en TEXT NOT NULL DEFAULT '',
                name_ar TEXT NOT NULL DEFAULT '',
                display_order INTEGER NOT NULL DEFAULT 0,
                is_system INTEGER NOT NULL DEFAULT 0,
                is_archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                name_en TEXT NOT NULL DEFAULT '',
                name_ar TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                category_id INTEGER,
                price_minor INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                image_filename TEXT NOT NULL DEFAULT '',
                image_source TEXT NOT NULL DEFAULT '',
                image_attribution TEXT NOT NULL DEFAULT '',
                image_license TEXT NOT NULL DEFAULT '',
                image_source_url TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT NOT NULL UNIQUE,
                receipt_number TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                currency TEXT NOT NULL,
                subtotal_minor INTEGER NOT NULL,
                tax_minor INTEGER NOT NULL DEFAULT 0,
                discount_minor INTEGER NOT NULL DEFAULT 0,
                total_minor INTEGER NOT NULL,
                cash_received_minor INTEGER NOT NULL,
                change_minor INTEGER NOT NULL,
                business_name TEXT NOT NULL,
                business_address TEXT NOT NULL DEFAULT '',
                business_phone TEXT NOT NULL DEFAULT '',
                cashier_user_id INTEGER,
                cashier_name_snapshot TEXT NOT NULL DEFAULT '',
                payment_summary TEXT NOT NULL DEFAULT '',
                client_tx_key TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER,
                product_name TEXT NOT NULL,
                product_name_en TEXT NOT NULL DEFAULT '',
                product_name_ar TEXT NOT NULL DEFAULT '',
                category_name TEXT NOT NULL DEFAULT '',
                unit_price_minor INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                line_total_minor INTEGER NOT NULL
            );
            CREATE TABLE payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                payment_method TEXT NOT NULL CHECK (payment_method IN ('CASH', 'CARD', 'INSTAPAY', 'EWALLET', 'CUSTOM')),
                payment_provider TEXT NOT NULL DEFAULT '',
                custom_method_label TEXT NOT NULL DEFAULT '',
                payment_amount_minor INTEGER NOT NULL CHECK (payment_amount_minor >= 0),
                currency TEXT NOT NULL DEFAULT 'USD',
                payment_status TEXT NOT NULL CHECK (payment_status IN ('CASH_SETTLED', 'CONFIRMED_EXTERNAL')),
                external_reference TEXT NOT NULL DEFAULT '',
                confirmed_at TEXT NOT NULL DEFAULT '',
                confirmed_by_user_id INTEGER,
                confirmed_by_name TEXT NOT NULL DEFAULT '',
                verification_source TEXT NOT NULL DEFAULT 'local',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            INSERT INTO app_meta VALUES ('seeded_defaults', '1'), ('schema_version', '5');
            INSERT INTO business_settings (id, business_name, accent_color, currency, business_country)
                VALUES (1, 'Legacy V5 Roastery', '#005B37', 'EGP', 'EG');
            INSERT INTO products (id, name, name_en, category, price_minor, created_at, updated_at)
                VALUES (1, 'Flat White', 'Flat White', 'Espresso', 6500, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
            INSERT INTO orders (
                id, order_number, receipt_number, created_at, currency, subtotal_minor,
                total_minor, cash_received_minor, change_minor, business_name, payment_summary
            ) VALUES (
                1, 'ORD-1001', 'RCP-1001', '2026-01-02T12:00:00Z', 'EGP', 6500,
                6500, 6500, 0, 'Legacy V5 Roastery', 'CARD'
            );
            INSERT INTO order_items (id, order_id, product_id, product_name, unit_price_minor, quantity, line_total_minor)
                VALUES (1, 1, 1, 'Flat White', 6500, 1, 6500);
            INSERT INTO payments (
                id, order_id, payment_method, payment_provider, payment_amount_minor,
                currency, payment_status, external_reference, confirmed_at, verification_source
            ) VALUES (
                1, 1, 'CARD', 'Meeza', 6500, 'EGP', 'CONFIRMED_EXTERNAL', 'TERM-009', '2026-01-02T12:00:00Z', 'cashier_manual_confirmation'
            );
            """
        )

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        ver = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version';").fetchone()[0]
    assert ver == str(CURRENT_SCHEMA_VERSION)

    cfg = db.get_settings()
    assert cfg.country_configured is False
    assert cfg.business_name == "Legacy V5 Roastery"

    # Existing historical CARD order is completely preserved
    hist = db.get_order(1)
    assert hist is not None
    assert hist.primary_payment is not None
    assert hist.primary_payment.payment_method == METHOD_CARD
    assert hist.primary_payment.payment_provider == "Meeza"
    assert hist.primary_payment.external_reference == "TERM-009"

    # Newly rebuilt payments table accepts APPLE_PAY without CHECK constraint error
    prod = db.list_products()[0]
    ap_order = db.create_order_with_payment(
        [CartLine(product_id=prod.id, name=prod.name, unit_price_minor=6500, quantity=1)],
        payment_method=METHOD_APPLE_PAY,
        payment_provider="Apple Pay",
        external_reference="AP-1234",
        confirmed_external=True,
    )
    assert ap_order.primary_payment is not None
    assert ap_order.primary_payment.payment_method == METHOD_APPLE_PAY
    assert ap_order.primary_payment.payment_status == STATUS_CONFIRMED_EXTERNAL

