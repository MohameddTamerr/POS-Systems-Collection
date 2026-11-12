from __future__ import annotations

import json
import sqlite3
import string
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cafe_pos.auth import ROLE_CASHIER
from cafe_pos.backups import create_backup_archive, restore_backup_archive
from cafe_pos.config import BusinessConfig
from cafe_pos.db import CartLine, Database
from cafe_pos.i18n import (
    bidi_ltr,
    format_chart_bucket_label,
    format_header_date,
    format_timestamp_display,
    get_language,
    load_catalog,
    normalize_digits,
    set_language,
    tr_audit_action,
    tr_count,
)
from cafe_pos.image_search import suggest_image_queries
from cafe_pos.money import format_minor, parse_major_to_minor
from cafe_pos.paths import get_backups_dir, get_database_path, get_translations_dir
from cafe_pos.receipts import build_receipt_document, build_receipt_plain_text, export_receipt_pdf
from cafe_pos.ui.main_window import MainWindow
from cafe_pos.ui.payment_dialog import CheckoutPaymentDialog
from cafe_pos.ui.setup_dialog import SetupDialog
from cafe_pos.ui.widgets import ProductQtyControl


def _extract_placeholders(template: str) -> set[str]:
    return {
        field_name
        for _lit, field_name, _fmt, _conv in string.Formatter().parse(template)
        if field_name is not None
    }


def test_translation_catalog_parity_and_placeholders():
    trans_dir = get_translations_dir()
    en_path = trans_dir / "en.json"
    ar_path = trans_dir / "ar_EG.json"
    assert en_path.is_file()
    assert ar_path.is_file()

    en_data = json.loads(en_path.read_text(encoding="utf-8"))
    ar_data = json.loads(ar_path.read_text(encoding="utf-8"))

    assert set(en_data.keys()) == set(ar_data.keys())
    assert len(en_data) >= 350

    load_catalog.cache_clear()
    en_loaded = load_catalog("en")
    ar_loaded = load_catalog("ar-EG")
    assert set(en_loaded.keys()) == set(ar_loaded.keys())

    for key, en_val in en_data.items():
        ar_val = ar_data[key]
        assert isinstance(en_val, str) and en_val.strip() != "", f"Empty EN value: {key}"
        assert isinstance(ar_val, str) and ar_val.strip() != "", f"Empty AR value: {key}"
        assert _extract_placeholders(en_val) == _extract_placeholders(ar_val), (
            f"Placeholder mismatch for key '{key}': EN={_extract_placeholders(en_val)} AR={_extract_placeholders(ar_val)}"
        )


def test_i18n_plurals_bidi_digits_currency_and_dates():
    # Arabic-Indic digit normalization & EGP input parsing
    assert normalize_digits("١٢٥٫٥٠") == "125.50"
    assert parse_major_to_minor("١٢٥٫٥٠", "EGP") == 12550

    # Bidi LTR isolation wrappers
    iso = bidi_ltr("ORD-1001")
    assert iso.startswith("\u2066") and iso.endswith("\u2069")

    # Arabic plural forms (zero / singular / dual / few / many)
    assert tr_count("item", 0, lang="ar-EG") == "0 أصناف"
    assert tr_count("item", 1, lang="ar-EG") == "صنف واحد"
    assert tr_count("item", 2, lang="ar-EG") == "صنفان"
    assert tr_count("item", 5, lang="ar-EG") == "5 أصناف"
    assert tr_count("item", 11, lang="ar-EG") == "11 صنف"
    assert tr_count("item", 105, lang="ar-EG") == "105 أصناف"
    assert tr_count("item", 125, lang="ar-EG") == "125 صنف"

    # EGP symbol localization & bidi isolation
    egp_ar = format_minor(4500, "EGP", lang="ar-EG")
    assert "\u206645.00\u2069" in egp_ar
    assert "ج.م" in egp_ar
    assert format_minor(4500, "EGP", lang="en") == "E£45.00"

    # Header dates, timestamps & chart bucket labels
    hdr_ar = format_header_date(datetime(2026, 11, 12), lang="ar-EG")
    assert "نوفمبر" in hdr_ar
    ts_ar = format_timestamp_display("2026-11-12T10:30:00+00:00", lang="ar-EG")
    assert "\u2066" in ts_ar and ("ص" in ts_ar or "م" in ts_ar)
    assert format_chart_bucket_label("2026-11-12", lang="ar-EG") == "12 نوفمبر"
    assert format_chart_bucket_label("14:00", lang="ar-EG") == "2 م"


def test_offline_arabic_image_query_suggestions():
    latte_qs = suggest_image_queries("لاتيه مثلج (كبير)", "Cold Drinks")
    assert len(latte_qs) >= 2
    assert any("iced latte" in q.lower() for q in latte_qs)

    croissant_qs = suggest_image_queries("كرواسون زبدة", "Bakery")
    assert any("butter croissant" in q.lower() for q in croissant_qs)

    matcha_qs = suggest_image_queries("شاي ماتشا", "Tea & Matcha")
    assert any("matcha" in q.lower() for q in matcha_qs)


def test_schema_v4_migration_and_zip_backup_preserves_language_prefs(
    isolated_data_dir: Path, tmp_path: Path
):
    # Create an authentic pre-v4 (v3) DB missing app_language, receipt_language, preferred_language
    v3_path = get_database_path()
    with sqlite3.connect(str(v3_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                display_name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL DEFAULT '',
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE business_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                business_name TEXT NOT NULL,
                accent_color TEXT NOT NULL,
                currency TEXT NOT NULL,
                address TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                logo_blob BLOB NOT NULL DEFAULT X'',
                logo_ext TEXT NOT NULL DEFAULT '',
                setup_completed INTEGER NOT NULL DEFAULT 0,
                next_order_seq INTEGER NOT NULL DEFAULT 1001,
                image_provider TEXT NOT NULL DEFAULT 'openverse',
                pexels_api_key TEXT NOT NULL DEFAULT '',
                reduced_motion INTEGER NOT NULL DEFAULT 0,
                auto_lock_minutes INTEGER NOT NULL DEFAULT 0,
                backup_enabled INTEGER NOT NULL DEFAULT 0,
                backup_frequency TEXT NOT NULL DEFAULT 'daily',
                backup_destination TEXT NOT NULL DEFAULT '',
                backup_retention_count INTEGER NOT NULL DEFAULT 7,
                last_auto_backup_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
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
                created_at TEXT NOT NULL,
                currency TEXT NOT NULL,
                subtotal_minor INTEGER NOT NULL,
                total_minor INTEGER NOT NULL,
                cash_received_minor INTEGER NOT NULL,
                change_minor INTEGER NOT NULL,
                business_name TEXT NOT NULL,
                business_address TEXT NOT NULL DEFAULT '',
                business_phone TEXT NOT NULL DEFAULT '',
                cashier_user_id INTEGER,
                cashier_name_snapshot TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER,
                product_name TEXT NOT NULL,
                unit_price_minor INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                line_total_minor INTEGER NOT NULL
            );
            INSERT INTO app_meta VALUES ('seeded_defaults', '1'), ('schema_version', '3');
            INSERT INTO business_settings (
                id, business_name, accent_color, currency, setup_completed
            ) VALUES (1, 'Nile Brew', '#005B37', 'EGP', 1);
            INSERT INTO products VALUES (
                1, 'Turkish Coffee', 'Espresso', 4500, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',
                '', '', '', '', ''
            );
            """
        )

    from cafe_pos.db import CURRENT_SCHEMA_VERSION

    db = Database(v3_path)
    with sqlite3.connect(str(v3_path)) as conn:
        row = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version';").fetchone()
    assert row is not None and row[0] == str(CURRENT_SCHEMA_VERSION)

    cfg = db.get_settings()
    assert cfg.app_language == "en"
    assert cfg.receipt_language == "follow"
    assert cfg.business_name == "Nile Brew"

    saved_cfg, admin = db.initialize_owner_and_business(
        business_cfg=BusinessConfig(
            business_name="Nile Brew",
            currency="EGP",
            app_language="ar-EG",
            receipt_language="en",
            setup_completed=True,
        ),
        admin_username="owner",
        admin_password="OwnerStrong!2026",
    )
    db.set_session_actor(admin)
    c1 = db.create_user(
        "sara",
        "CashierStrong!2026",
        ROLE_CASHIER,
        display_name="سارة",
        preferred_language="ar-EG",
        actor=admin,
    )
    assert c1.preferred_language == "ar-EG"

    bkp_path = create_backup_archive(
        db, get_backups_dir() / "v4_lang_backup.zip", backup_kind="manual", actor=admin
    )

    # Mutate settings & user language back to English, then restore ZIP -> must restore Arabic prefs
    saved_cfg.app_language = "en"
    saved_cfg.receipt_language = "follow"
    db.save_settings(saved_cfg, actor=admin)
    db.update_user(c1.id, preferred_language="", actor=admin)
    assert db.get_settings().app_language == "en"

    restore_backup_archive(
        db,
        bkp_path,
        actor=admin,
        admin_password="OwnerStrong!2026",
        verify_admin_password=True,
    )
    restored_cfg = db.get_settings()
    assert restored_cfg.app_language == "ar-EG"
    assert restored_cfg.receipt_language == "en"
    restored_users = db.list_users(actor=admin)
    sara = next(u for u in restored_users if u.username == "sara")
    assert sara.preferred_language == "ar-EG"


def test_setup_dialog_live_bilingual_switch_preserves_inputs_and_direction(qapp):
    dlg = SetupDialog(BusinessConfig(), is_first_launch=True, require_admin_setup=True)
    dlg.name_edit.setText("كافيه النيل")
    idx_egp = dlg.currency_combo.findData("EGP")
    dlg.currency_combo.setCurrentIndex(idx_egp)
    dlg._set_step(1)
    dlg.address_edit.setText("15 شارع التحرير")
    dlg.admin_username_edit.setText("nile_admin")
    dlg.admin_display_edit.setText("محمد تامر")
    dlg.admin_password_edit.setText("StrongPass#123")
    dlg.admin_confirm_edit.setText("MismatchPass#123")
    dlg._on_save()
    assert not dlg.validation_lbl.isHidden()

    # Toggle to Arabic live -> flips RTL, preserves step, inputs & updates validation banner in Arabic
    dlg.lang_selector._on_pick("ar-EG")
    assert get_language() == "ar-EG"
    assert dlg.layoutDirection() == Qt.RightToLeft
    assert dlg.step_stack.currentIndex() == 1
    assert dlg.name_edit.text() == "كافيه النيل"
    assert dlg.currency_combo.currentData() == "EGP"
    assert dlg.address_edit.text() == "15 شارع التحرير"
    assert dlg.admin_username_edit.text() == "nile_admin"
    assert "غير متطابقتين" in dlg.validation_lbl.text()

    # Toggle back to English -> flips LTR, preserves everything
    dlg.lang_selector._on_pick("en")
    assert get_language() == "en"
    assert dlg.layoutDirection() == Qt.LeftToRight
    assert dlg.name_edit.text() == "كافيه النيل"
    assert "do not match" in dlg.validation_lbl.text().lower()
    dlg.close()


def test_login_selector_persists_prelogin_and_user_preferred_language_override(
    qapp, isolated_data_dir: Path
):
    db = Database(get_database_path())
    _, admin = db.initialize_owner_and_business(
        business_cfg=BusinessConfig(business_name="Kemet Coffee", app_language="en", currency="EGP"),
        admin_username="owner",
        admin_password="OwnerPassword!2026",
    )
    db.set_session_actor(admin)
    c_ar = db.create_user(
        "cashier_ar",
        "CashierPass!2026",
        ROLE_CASHIER,
        display_name="أحمد علي",
        preferred_language="ar-EG",
        actor=admin,
    )
    c_default = db.create_user(
        "cashier_def",
        "CashierPass!2026",
        ROLE_CASHIER,
        display_name="John Doe",
        preferred_language="",
        actor=admin,
    )
    db.set_session_actor(None)

    window = MainWindow(db, qapp)
    assert window.layoutDirection() == Qt.LeftToRight

    # Pre-login selector toggles store default language & persists to DB
    window.login_view.lang_selector._on_pick("ar-EG")
    assert get_language() == "ar-EG"
    assert db.get_settings().app_language == "ar-EG"
    assert window.layoutDirection() == Qt.RightToLeft
    assert QApplication.layoutDirection() == Qt.RightToLeft
    window.close()

    # Simulated restart boots directly in ar-EG + RTL
    set_language("en")
    db2 = Database(get_database_path())
    assert db2.get_settings().app_language == "ar-EG"
    # Boot cleanly via main startup simulation: set app language from DB
    set_language(db2.get_settings().app_language)
    window2 = MainWindow(db2, qapp)
    assert window2.layoutDirection() == Qt.RightToLeft

    # Switch store default back to English pre-login
    window2.login_view.lang_selector._on_pick("en")
    assert db2.get_settings().app_language == "en"
    assert window2.layoutDirection() == Qt.LeftToRight

    # Sign in as Cashier with preferred_language="ar-EG" -> UI flips RTL without overwriting store default
    window2.login_user(c_ar)
    assert get_language() == "ar-EG"
    assert window2.layoutDirection() == Qt.RightToLeft
    assert db2.get_settings().app_language == "en"

    # Logout reverts UI cleanly back to store default "en"
    window2.logout(reason="manual")
    assert get_language() == "en"
    assert window2.layoutDirection() == Qt.LeftToRight

    # Sign in as Cashier with preferred_language="" -> follows store default "en"
    window2.login_user(c_default)
    assert get_language() == "en"
    assert window2.layoutDirection() == Qt.LeftToRight
    window2.close()


def test_live_switch_preserves_cart_cash_search_tab_and_unsaved_settings(
    qapp, isolated_data_dir: Path
):
    db = Database(get_database_path())
    cfg, admin = db.initialize_owner_and_business(
        business_cfg=BusinessConfig(business_name="Roast & Ember Café", currency="EGP", app_language="en"),
        admin_username="owner",
        admin_password="OwnerPassword!2026",
    )
    window = MainWindow(db, qapp)
    window.login_user(admin)
    window.show()
    qapp.processEvents()

    products = db.list_products()
    window.register_view.add_product_to_cart(products[0])
    window.register_view.add_product_to_cart(products[1])
    window.register_view.search_input.setText("Latte")

    # Stage unsaved edits on SettingsView
    window.switch_view(5)
    sv = window.settings_view
    sv.name_edit.setText("Unsaved Cairo Roastery")
    sv.address_edit.setText("Unsaved Zamalek St")
    sv._set_accent("#2B4C65")

    # Live switch to Arabic from Settings Section 1 without clicking Save Settings
    sv.lang_selector._on_pick("ar-EG")
    qapp.processEvents()

    assert get_language() == "ar-EG"
    assert window.layoutDirection() == Qt.RightToLeft
    assert window.stack.currentIndex() == 5
    # Unsaved Settings edits MUST stay intact
    assert sv.name_edit.text() == "Unsaved Cairo Roastery"
    assert sv.address_edit.text() == "Unsaved Zamalek St"
    assert sv._draft_accent == "#2B4C65"
    # Settings group titles localized live
    assert "اللغة والمنطقة والعملة" in sv.lang_group.title()
    assert "هوية المتجر" in sv.profile_group.title()

    # Register cart & search filter MUST stay intact
    reg = window.register_view
    assert len(reg.cart) == 2
    assert reg.search_input.text() == "Latte"

    # CheckoutPaymentDialog handles Arabic-Indic cash entry & live RTL retranslation cleanly
    pay_dlg = CheckoutPaymentDialog(db, cfg, list(reg.cart.values()), parent=window)
    pay_dlg.cash_edit.setText("١٥٠٫٢٥")
    _cnt, total_minor = reg.cart_totals_minor()
    expected_change = 15025 - total_minor
    expected_str = format_minor(expected_change, "EGP", lang="ar-EG")
    assert pay_dlg.cash_change_val_lbl.text() == expected_str
    assert pay_dlg.confirm_btn.isEnabled() is True
    assert pay_dlg.cash_edit.alignment() == Qt.AlignRight
    pay_dlg.close()

    window.close()


def test_product_qty_control_mirrored_hitboxes_and_keys_in_ltr_vs_rtl(qapp):
    ctrl = ProductQtyControl("#005B37", "Flat White")
    ctrl.show()
    qapp.processEvents()

    inc_hits: list[int] = []
    dec_hits: list[int] = []
    ctrl.increment_clicked.connect(lambda: inc_hits.append(1))
    ctrl.decrement_clicked.connect(lambda: dec_hits.append(1))

    left_pt = QPoint(14, 16)
    right_pt = QPoint(72, 16)

    # LTR mode: [ + ] sits on right when qty=0; expanded is [ - ] N [ + ]
    set_language("en")
    ctrl.setLayoutDirection(Qt.LeftToRight)
    ctrl.retranslate_ui()
    ctrl.set_quantity(0, animate=False)

    QTest.mouseClick(ctrl, Qt.LeftButton, Qt.NoModifier, left_pt)
    assert len(inc_hits) == 0
    QTest.mouseClick(ctrl, Qt.LeftButton, Qt.NoModifier, right_pt)
    assert len(inc_hits) == 1

    ctrl.set_quantity(2, animate=False)
    QTest.mouseClick(ctrl, Qt.LeftButton, Qt.NoModifier, left_pt)
    QTest.mouseClick(ctrl, Qt.LeftButton, Qt.NoModifier, right_pt)
    assert len(dec_hits) == 1
    assert len(inc_hits) == 2

    QTest.keyClick(ctrl, Qt.Key_Right)
    QTest.keyClick(ctrl, Qt.Key_Left)
    assert len(inc_hits) == 3
    assert len(dec_hits) == 2

    # RTL mode: [ + ] sits on LEFT when qty=0; expanded is mirrored [ + ] N [ - ]
    set_language("ar-EG")
    ctrl.setLayoutDirection(Qt.RightToLeft)
    ctrl.retranslate_ui()
    ctrl.set_quantity(0, animate=False)

    QTest.mouseClick(ctrl, Qt.LeftButton, Qt.NoModifier, right_pt)
    assert len(inc_hits) == 3  # Right edge does NOT trigger + in RTL when collapsed
    QTest.mouseClick(ctrl, Qt.LeftButton, Qt.NoModifier, left_pt)
    assert len(inc_hits) == 4  # Left edge triggers + in RTL

    ctrl.set_quantity(2, animate=False)
    QTest.mouseClick(ctrl, Qt.LeftButton, Qt.NoModifier, left_pt)
    QTest.mouseClick(ctrl, Qt.LeftButton, Qt.NoModifier, right_pt)
    assert len(inc_hits) == 5  # Left triggers + in RTL
    assert len(dec_hits) == 3  # Right triggers - in RTL

    QTest.keyClick(ctrl, Qt.Key_Left)
    QTest.keyClick(ctrl, Qt.Key_Right)
    assert len(inc_hits) == 6
    assert len(dec_hits) == 4

    ctrl.close()


def test_audit_canonical_storage_and_bilingual_receipt_pdf_modes(
    qapp, isolated_data_dir: Path, tmp_path: Path
):
    db = Database(get_database_path())
    cfg, admin = db.initialize_owner_and_business(
        business_cfg=BusinessConfig(
            business_name="كافيه قمر",
            address="وسط البلد، القاهرة",
            phone="01001234567",
            currency="EGP",
            app_language="en",
            receipt_language="follow",
        ),
        admin_username="owner",
        admin_password="OwnerPassword!2026",
        admin_display_name="طارق محمود",
    )
    db.set_session_actor(admin)
    prod = db.add_product("فلات وايت", "Espresso", 6500, actor=admin)
    order = db.create_order(
        [CartLine(product_id=prod.id, name=prod.name, unit_price_minor=6500, quantity=2)],
        20000,
        actor=admin,
    )

    # DB stores canonical uppercase ASCII actions regardless of display language
    with sqlite3.connect(str(get_database_path())) as conn:
        raw_actions = {r[0] for r in conn.execute("SELECT action FROM audit_logs;").fetchall()}
    assert "ADMIN_INITIALIZED" in raw_actions
    assert "ORDER_CREATED" in raw_actions
    assert tr_audit_action("ORDER_CREATED", lang="ar-EG") == "إتمام طلب بيع"

    # UsersView translates Audit Log in Arabic & filters by translated Arabic action text
    window = MainWindow(db, qapp)
    window.login_user(admin)
    window.switch_language("ar-EG", persist_store=False)
    window.switch_view(4)
    uv = window.users_view
    uv._switch_subtab("audit")
    assert uv.audit_table.rowCount() >= 2
    displayed_actions = {
        uv.audit_table.item(r, 2).text() for r in range(uv.audit_table.rowCount())
    }
    assert "إتمام طلب بيع" in displayed_actions
    uv.search_input.setText("إتمام طلب بيع")
    assert uv.audit_table.rowCount() >= 1

    # Receipt "follow" mode follows active Arabic UI, "en" forces English, "ar-EG" forces Arabic
    full_order = db.get_order(order.id, actor=admin)
    assert full_order is not None

    cfg_follow = BusinessConfig(
        business_name="كافيه قمر", currency="EGP", app_language="ar-EG", receipt_language="follow"
    )
    html_follow_ar = build_receipt_document(full_order, cfg_follow).toHtml()
    assert "الإجمالي" in html_follow_ar
    assert "ج.م" in html_follow_ar
    assert "فلات وايت" in html_follow_ar

    cfg_en = BusinessConfig(
        business_name="كافيه قمر", currency="EGP", app_language="ar-EG", receipt_language="en"
    )
    txt_en = build_receipt_plain_text(full_order, cfg_en)
    assert "TOTAL" in txt_en
    assert "E£130.00" in txt_en

    pdf_ar = tmp_path / "receipt_a5_ar.pdf"
    export_receipt_pdf(pdf_ar, full_order, cfg_follow)
    assert pdf_ar.exists() and pdf_ar.read_bytes().startswith(b"%PDF-")

    window.close()
