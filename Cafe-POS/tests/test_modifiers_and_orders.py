from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QMessageBox

from cafe_pos.auth import ROLE_CASHIER, AuthorizationError
from cafe_pos.backups import create_backup_archive, inspect_and_validate_backup, restore_backup_archive
from cafe_pos.config import BusinessConfig
from cafe_pos.db import (
    CURRENT_SCHEMA_VERSION,
    HELD_STATUS_CANCELLED,
    HELD_STATUS_COMPLETED,
    HELD_STATUS_HELD,
    MODIFIER_MODE_MULTI,
    MODIFIER_MODE_SINGLE,
    ORDER_TYPE_DELIVERY,
    ORDER_TYPE_DINE_IN,
    ORDER_TYPE_TAKEAWAY,
    CartLine,
    CartModifierSelection,
    Database,
)
from cafe_pos.paths import get_backups_dir, get_database_path
from cafe_pos.receipts import build_receipt_document, build_receipt_plain_text
from cafe_pos.ui.held_orders_dialog import HeldOrdersDialog
from cafe_pos.ui.main_window import MainWindow
from cafe_pos.ui.modifier_manager_dialog import ModifierManagerDialog
from cafe_pos.ui.payment_dialog import CheckoutPaymentDialog
from cafe_pos.ui.product_customize_dialog import ProductCustomizeDialog
from cafe_pos.ui.product_editor_dialog import ProductEditorDialog


def _setup_owner_and_cashier(db: Database):
    cfg = BusinessConfig(
        business_name="Cedar & Bean Café",
        accent_color="#005B37",
        currency="USD",
        address="12 Nile Corniche",
        phone="+20 100 555 0101",
        setup_completed=True,
    )
    saved_cfg, admin = db.initialize_owner_and_business(
        business_cfg=cfg,
        admin_username="owner",
        admin_password="OwnerStrongPassword!1",
        admin_display_name="Owner Admin",
    )
    cashier = db.create_user(
        "sara",
        "CashierStrong!2026",
        ROLE_CASHIER,
        display_name="Sara Barista",
        actor=admin,
    )
    return saved_cfg, admin, cashier


def _seed_sample_modifiers(db: Database, products, admin):
    latte = next(p for p in products if "Flat White" in p.name or "Latte" in p.name)
    espresso = next(p for p in products if "Espresso" in p.name)
    size_grp = db.create_modifier_group(
        "Size",
        name_ar="الحجم",
        selection_mode=MODIFIER_MODE_SINGLE,
        is_required=True,
        min_select=1,
        max_select=1,
        options=[
            {"name_en": "Small", "name_ar": "صغير", "price_delta_minor": 0},
            {"name_en": "Medium", "name_ar": "وسط", "price_delta_minor": 100},
            {"name_en": "Large", "name_ar": "كبير", "price_delta_minor": 200},
        ],
        product_ids=[latte.id, espresso.id],
        actor=admin,
    )
    extras_grp = db.create_modifier_group(
        "Extras",
        name_ar="إضافات",
        selection_mode=MODIFIER_MODE_MULTI,
        is_required=False,
        min_select=0,
        max_select=2,
        options=[
            {"name_en": "Extra Shot", "name_ar": "شوت إضافي", "price_delta_minor": 150},
            {"name_en": "Oat Milk", "name_ar": "حليب شوفان", "price_delta_minor": 125},
            {"name_en": "Vanilla Syrup", "name_ar": "شراب فانيليا", "price_delta_minor": 75},
        ],
        product_ids=[latte.id],
        actor=admin,
    )
    return latte, espresso, size_grp, extras_grp


# =====================================================================
# 1. SAFE SCHEMA v6 -> v7 MIGRATION & BACKUP/RESTORE COMPATIBILITY
# =====================================================================


def test_schema_v6_migration_preserves_data_and_backup_roundtrip(
    isolated_data_dir: Path,
):
    db_path = get_database_path()
    # Build a synthetic Schema v6 database without v7 tables/columns
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                normalized_key TEXT NOT NULL UNIQUE,
                name_en TEXT NOT NULL DEFAULT '',
                name_ar TEXT NOT NULL DEFAULT '',
                display_order INTEGER NOT NULL DEFAULT 100,
                is_system INTEGER NOT NULL DEFAULT 0,
                is_archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                price_minor INTEGER NOT NULL,
                image_filename TEXT NOT NULL DEFAULT '',
                image_source TEXT NOT NULL DEFAULT '',
                image_attribution TEXT NOT NULL DEFAULT '',
                image_license TEXT NOT NULL DEFAULT '',
                image_source_url TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                name_en TEXT NOT NULL DEFAULT '',
                name_ar TEXT NOT NULL DEFAULT '',
                category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                sku TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT NOT NULL DEFAULT '',
                last_login_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                preferred_language TEXT NOT NULL DEFAULT ''
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
                cashier_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                cashier_name_snapshot TEXT NOT NULL DEFAULT '',
                receipt_number TEXT NOT NULL DEFAULT '',
                tax_minor INTEGER NOT NULL DEFAULT 0,
                discount_minor INTEGER NOT NULL DEFAULT 0,
                client_tx_key TEXT NOT NULL DEFAULT '',
                business_country_snapshot TEXT NOT NULL DEFAULT 'EG'
            );
            CREATE TABLE order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                product_id INTEGER,
                product_name TEXT NOT NULL,
                unit_price_minor INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                line_total_minor INTEGER NOT NULL,
                product_name_en TEXT NOT NULL DEFAULT '',
                product_name_ar TEXT NOT NULL DEFAULT '',
                category_snapshot TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                payment_method TEXT NOT NULL,
                payment_provider TEXT NOT NULL DEFAULT '',
                custom_method_label TEXT NOT NULL DEFAULT '',
                payment_amount_minor INTEGER NOT NULL,
                currency TEXT NOT NULL,
                payment_status TEXT NOT NULL,
                external_reference TEXT NOT NULL DEFAULT '',
                confirmed_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                confirmed_by_name TEXT NOT NULL DEFAULT '',
                confirmed_at TEXT NOT NULL,
                verification_source TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE translation_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_text TEXT NOT NULL,
                source_lang TEXT NOT NULL,
                target_text TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                confidence TEXT NOT NULL DEFAULT 'medium',
                source TEXT NOT NULL DEFAULT 'offline_dict',
                is_user_confirmed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_text, source_lang, target_lang)
            );
            CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                actor_username TEXT NOT NULL DEFAULT '',
                actor_role TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                target_type TEXT NOT NULL DEFAULT '',
                target_id TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT ''
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
                last_auto_backup_at TEXT NOT NULL DEFAULT '',
                app_language TEXT NOT NULL DEFAULT 'en',
                receipt_language TEXT NOT NULL DEFAULT 'follow_app',
                business_country TEXT NOT NULL DEFAULT 'EG',
                payment_methods_json TEXT NOT NULL DEFAULT '',
                auto_save_receipt_pdf INTEGER NOT NULL DEFAULT 0,
                receipt_export_dir TEXT NOT NULL DEFAULT '',
                online_translation_enabled INTEGER NOT NULL DEFAULT 0,
                country_configured INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO categories (normalized_key, name_en, name_ar, display_order, is_system, is_archived, created_at, updated_at)
            VALUES ('coffee', 'Coffee', 'قهوة', 10, 1, 0, '2026-01-01T10:00:00+00:00', '2026-01-01T10:00:00+00:00');
            INSERT INTO products (name, category, price_minor, created_at, updated_at, name_en, name_ar, category_id)
            VALUES ('Legacy Mocha', 'Coffee', 450, '2026-01-01T10:00:00+00:00', '2026-01-01T10:00:00+00:00', 'Legacy Mocha', 'موكا', 1);
            INSERT INTO business_settings (id, business_name, accent_color, currency, setup_completed, next_order_seq)
            VALUES (1, 'Migrated V6 Café', '#005B37', 'USD', 1, 1002);
            INSERT INTO orders (order_number, created_at, currency, subtotal_minor, total_minor, cash_received_minor, change_minor, business_name, receipt_number)
            VALUES ('ORD-1001', '2026-01-01T10:05:00+00:00', 'USD', 450, 450, 500, 50, 'Migrated V6 Café', 'RCP-1001');
            INSERT INTO order_items (order_id, product_id, product_name, unit_price_minor, quantity, line_total_minor)
            VALUES (1, 1, 'Legacy Mocha', 450, 1, 450);
            INSERT INTO payments (order_id, payment_method, payment_amount_minor, currency, payment_status, confirmed_at, verification_source)
            VALUES (1, 'CASH', 450, 'USD', 'CASH_SETTLED', '2026-01-01T10:05:00+00:00', 'cashier_cash_drawer');
            INSERT INTO app_meta (key, value) VALUES ('schema_version', '6'), ('seeded_categories_v5', '1'), ('seeded_defaults', '1');
            """
        )
        conn.commit()
    finally:
        conn.close()

    # Open through Database -> safe automatic v6 -> v7 upgrade
    db = Database(db_path)
    cfg = db.get_settings()
    assert cfg.business_name == "Migrated V6 Café"

    prods = db.list_products()
    assert len(prods) == 1 and prods[0].name == "Legacy Mocha"

    legacy_order = db.get_order(1)
    assert legacy_order is not None
    assert legacy_order.order_number == "ORD-1001"
    assert legacy_order.order_type == ORDER_TYPE_DINE_IN
    assert legacy_order.items[0].base_unit_price_minor == 450
    assert legacy_order.items[0].modifiers == ()

    # Verify schema_version bumped to CURRENT_SCHEMA_VERSION and v7 tables are functional
    ro = sqlite3.connect(str(db_path))
    try:
        ver = ro.execute("SELECT value FROM app_meta WHERE key = 'schema_version';").fetchone()[0]
        assert int(ver) == CURRENT_SCHEMA_VERSION and CURRENT_SCHEMA_VERSION >= 8
        next_hold = ro.execute("SELECT next_hold_seq FROM business_settings WHERE id = 1;").fetchone()[0]
        assert int(next_hold) == 101
    finally:
        ro.close()

    # Verify backup & restore preserves v7 modifiers & held orders
    size_grp = db.create_modifier_group(
        "Size",
        name_ar="الحجم",
        selection_mode=MODIFIER_MODE_SINGLE,
        is_required=True,
        options=[
            {"name_en": "Regular", "name_ar": "عادي", "price_delta_minor": 0},
            {"name_en": "Large", "name_ar": "كبير", "price_delta_minor": 150},
        ],
        product_ids=[prods[0].id],
    )
    large_opt = size_grp.options[1]
    db.set_product_modifier_config(prods[0].id, [size_grp.id], {large_opt.id: 225})
    held = db.create_or_update_held_order(
        [
            CartLine(
                product_id=prods[0].id,
                name=prods[0].name,
                unit_price_minor=675,
                quantity=1,
                modifiers=(
                    CartModifierSelection(
                        group_id=size_grp.id,
                        option_id=large_opt.id,
                        group_name="Size",
                        option_name="Large",
                        price_delta_minor=225,
                    ),
                ),
            )
        ],
        order_type=ORDER_TYPE_DELIVERY,
        customer_name="Nadia",
        delivery_address="15 Maadi Rd",
    )
    assert db.count_held_orders() == 1

    bkp_path = get_backups_dir() / "test_v7_backup.zip"
    create_backup_archive(db, bkp_path, require_auth=False)
    info = inspect_and_validate_backup(bkp_path)
    assert info.schema_version == 8

    # Mutate live DB then restore
    db.cancel_held_order(held.id)
    assert db.count_held_orders() == 0
    restore_backup_archive(db, bkp_path)
    assert db.count_held_orders() == 1
    restored_mods = db.get_product_modifiers(prods[0].id)
    assert len(restored_mods) == 1
    assert restored_mods[0].options[1].resolved_price_delta_minor == 225


# =====================================================================
# 2. MODIFIERS CRUD, PER-PRODUCT OVERRIDES, VALIDATION & RBAC
# =====================================================================


def test_modifiers_backend_overrides_validation_archiving_and_rbac(
    isolated_data_dir: Path,
):
    db = Database(get_database_path())
    _cfg, admin, cashier = _setup_owner_and_cashier(db)
    products = db.list_products()
    latte, espresso, size_grp, extras_grp = _seed_sample_modifiers(db, products, admin)

    # RBAC: Cashier cannot create/update/archive modifier groups or set product overrides
    with pytest.raises(AuthorizationError):
        db.create_modifier_group(
            "Sugar",
            options=[{"name_en": "Less", "price_delta_minor": 0}],
            actor=cashier,
        )
    with pytest.raises(AuthorizationError):
        db.update_modifier_group(
            size_grp.id,
            "Size",
            options=[{"id": o.id, "name_en": o.name_en, "price_delta_minor": 0} for o in size_grp.options],
            actor=cashier,
        )
    with pytest.raises(AuthorizationError):
        db.archive_modifier_group(size_grp.id, archived=True, actor=cashier)
    with pytest.raises(AuthorizationError):
        db.set_product_modifier_config(latte.id, [size_grp.id], {}, actor=cashier)

    # Per-product option price override on Espresso vs Latte (same Size group)
    large_opt = next(o for o in size_grp.options if o.name_en == "Large")
    small_opt = next(o for o in size_grp.options if o.name_en == "Small")
    db.set_product_modifier_config(
        espresso.id,
        [size_grp.id],
        {large_opt.id: 350},
        actor=admin,
    )

    latte_mods = db.get_product_modifiers(latte.id)
    esp_mods = db.get_product_modifiers(espresso.id)
    assert [g.name_en for g in latte_mods] == ["Size", "Extras"]
    assert [g.name_en for g in esp_mods] == ["Size"]
    assert next(o for o in latte_mods[0].options if o.id == large_opt.id).resolved_price_delta_minor == 200
    assert next(o for o in esp_mods[0].options if o.id == large_opt.id).resolved_price_delta_minor == 350
    assert next(o for o in esp_mods[0].options if o.id == large_opt.id).has_price_override is True

    # Cashier checkout validation: required group missing raises ValueError
    with pytest.raises(ValueError, match="Size"):
        db.create_order(
            [CartLine(product_id=espresso.id, name=espresso.name, unit_price_minor=espresso.price_minor, quantity=1)],
            5000,
            actor=cashier,
        )

    # Single-select with >1 option rejected
    with pytest.raises(ValueError, match="Size"):
        db.create_order(
            [
                CartLine(
                    product_id=espresso.id,
                    name=espresso.name,
                    unit_price_minor=espresso.price_minor,
                    quantity=1,
                    modifiers=(
                        CartModifierSelection(group_id=size_grp.id, option_id=small_opt.id, group_name="Size", option_name="Small"),
                        CartModifierSelection(group_id=size_grp.id, option_id=large_opt.id, group_name="Size", option_name="Large"),
                    ),
                )
            ],
            5000,
            actor=cashier,
        )

    # Multi-select exceeding max_select (2) rejected
    extra_mods = tuple(
        CartModifierSelection(group_id=extras_grp.id, option_id=o.id, group_name="Extras", option_name=o.name_en)
        for o in extras_grp.options
    )
    with pytest.raises(ValueError, match="Extras"):
        db.create_order(
            [
                CartLine(
                    product_id=latte.id,
                    name=latte.name,
                    unit_price_minor=latte.price_minor,
                    quantity=1,
                    modifiers=(
                        CartModifierSelection(group_id=size_grp.id, option_id=small_opt.id, group_name="Size", option_name="Small"),
                        *extra_mods,
                    ),
                )
            ],
            5000,
            actor=cashier,
        )

    # Disabling a group hides it from active product modifiers
    db.update_modifier_group(
        extras_grp.id,
        extras_grp.name_en,
        name_ar=extras_grp.name_ar,
        selection_mode=extras_grp.selection_mode,
        is_required=extras_grp.is_required,
        min_select=extras_grp.min_select,
        max_select=extras_grp.max_select,
        is_active=False,
        options=[
            {
                "id": o.id,
                "name_en": o.name_en,
                "name_ar": o.name_ar,
                "price_delta_minor": o.price_delta_minor,
                "is_active": o.is_active,
            }
            for o in extras_grp.options
        ],
        actor=admin,
    )
    assert [g.name_en for g in db.get_product_modifiers(latte.id, include_inactive=False)] == ["Size"]

    # Archiving & restoring
    db.archive_modifier_group(size_grp.id, archived=True, actor=admin)
    assert db.get_product_modifiers(latte.id, include_inactive=False) == []
    assert [g.name_en for g in db.get_product_modifiers(latte.id, include_inactive=True)] == ["Extras"]
    assert any(g.is_archived for g in db.list_modifier_groups(include_archived=True))

    db.archive_modifier_group(size_grp.id, archived=False, actor=admin)
    assert len(db.get_product_modifiers(espresso.id)) == 1


# =====================================================================
# 3. MODIFIER UI WORKFLOWS & CART LINE SPLITTING / EDITING
# =====================================================================


def test_modifier_ui_dialogs_and_register_cart_splitting_merging(
    qapp, isolated_data_dir: Path, monkeypatch
):
    db = Database(get_database_path())
    saved_cfg, admin, cashier = _setup_owner_and_cashier(db)
    window = MainWindow(db, qapp)
    window.config = saved_cfg
    window._apply_branding()
    window.login_user(admin)

    products = db.list_products()
    latte, _esp, size_grp, extras_grp = _seed_sample_modifiers(db, products, admin)
    croissant = next(p for p in products if "Croissant" in p.name)

    # Test ProductCustomizeDialog directly: required rule gates confirm_btn, live price updates
    latte_groups = db.get_product_modifiers(latte.id)
    cust_dlg = ProductCustomizeDialog(saved_cfg, latte, latte_groups)
    assert cust_dlg.confirm_btn.isEnabled() is False
    small_opt = next(o for o in latte_groups[0].options if o.name_en == "Small")
    large_opt = next(o for o in latte_groups[0].options if o.name_en == "Large")
    shot_opt = next(o for o in latte_groups[1].options if o.name_en == "Extra Shot")
    oat_opt = next(o for o in latte_groups[1].options if o.name_en == "Oat Milk")
    syrup_opt = next(o for o in latte_groups[1].options if o.name_en == "Vanilla Syrup")

    cust_dlg._option_buttons[large_opt.id].click()
    assert cust_dlg.confirm_btn.isEnabled() is True
    assert cust_dlg.result_unit_price_minor == latte.price_minor + 200

    # Multi-select cap (max=2) keeps 3rd extra from overflowing
    cust_dlg._option_buttons[shot_opt.id].click()
    cust_dlg._option_buttons[oat_opt.id].click()
    cust_dlg._option_buttons[syrup_opt.id].click()
    assert len(cust_dlg._selected_option_ids_by_group[extras_grp.id]) == 2
    assert cust_dlg.result_unit_price_minor == latte.price_minor + 200 + 150 + 125
    cust_dlg.close()

    # Test ProductEditorDialog Modifiers Tab assignment + override save & clear
    window.switch_view(1)
    ed_dlg = ProductEditorDialog(
        saved_cfg,
        db.list_categories(),
        product=latte,
        db=db,
        is_admin=True,
    )
    assert len(ed_dlg._mod_group_checks) == 2
    ed_dlg._mod_override_edits[large_opt.id].setText("3.00")
    ed_dlg._on_save()
    assert ed_dlg.result() == QDialog.Accepted
    assert ed_dlg.result_option_price_overrides[large_opt.id] == 300
    db.set_product_modifier_config(
        latte.id,
        ed_dlg.result_modifier_group_ids,
        ed_dlg.result_option_price_overrides,
        actor=admin,
    )
    window._on_products_changed()

    # Switch to Register as Cashier
    window.login_user(cashier)
    reg = window.register_view

    # Product WITHOUT modifiers adds directly without opening customize modal
    customize_calls = 0
    orig_exec = ProductCustomizeDialog.exec

    def _fail_if_unexpected(self_dlg):
        nonlocal customize_calls
        customize_calls += 1
        return QDialog.Rejected

    monkeypatch.setattr(ProductCustomizeDialog, "exec", _fail_if_unexpected)
    reg.add_product_to_cart(croissant)
    assert customize_calls == 0
    assert reg.cart[croissant.id].quantity == 1

    # Product WITH modifiers opens customize modal; different combos stay separate, identical merge
    choice_plan = iter(
        [
            (small_opt.id,),
            (small_opt.id,),
            (large_opt.id, shot_opt.id),
        ]
    )

    def _auto_select_combo(self_dlg):
        opts = next(choice_plan)
        for oid in opts:
            self_dlg._option_buttons[oid].click()
        self_dlg._on_confirm()
        return QDialog.Accepted

    monkeypatch.setattr(ProductCustomizeDialog, "exec", _auto_select_combo)
    reg.add_product_to_cart(latte)
    reg.add_product_to_cart(latte)
    reg.add_product_to_cart(latte)

    latte_lines = [ln for ln in reg.cart.values() if ln.product_id == latte.id]
    assert len(latte_lines) == 2
    small_line = next(ln for ln in latte_lines if any(m.option_id == small_opt.id for m in ln.modifiers))
    large_line = next(ln for ln in latte_lines if any(m.option_id == large_opt.id for m in ln.modifiers))
    assert small_line.quantity == 2
    assert small_line.unit_price_minor == latte.price_minor
    assert large_line.quantity == 1
    assert large_line.unit_price_minor == latte.price_minor + 300 + 150

    # Product card badge aggregates total qty across both customized cart lines (2 + 1 = 3)
    assert reg._product_cards[latte.id].quantity == 3
    assert reg._product_cards[latte.id].qty_control.quantity == 3

    # Edit modifiers on large_line -> change to match small_line -> rows merge to qty=3
    def _change_to_small(self_dlg):
        self_dlg._option_buttons[shot_opt.id].click()  # uncheck Extra Shot
        self_dlg._option_buttons[small_opt.id].click()  # pick Small
        self_dlg._on_confirm()
        return QDialog.Accepted

    monkeypatch.setattr(ProductCustomizeDialog, "exec", _change_to_small)
    reg._edit_cart_line_modifiers(large_line.cart_key)
    latte_lines_after = [ln for ln in reg.cart.values() if ln.product_id == latte.id]
    assert len(latte_lines_after) == 1
    assert latte_lines_after[0].quantity == 3
    assert reg._product_cards[latte.id].quantity == 3

    monkeypatch.setattr(ProductCustomizeDialog, "exec", orig_exec)

    # Test ModifierManagerDialog create + save via UI (with Admin)
    window.login_user(admin)
    mgr = ModifierManagerDialog(db, saved_cfg)
    mgr._start_new_group()
    mgr.name_en_edit.setText("Sugar Level")
    mgr.name_ar_edit.setText("مستوى السكر")
    en_cell = mgr.options_table.cellWidget(0, 0)
    en_cell.setText("Less Sugar")
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: QMessageBox.Ok)
    mgr._save_current_group()
    assert any(g.name_en == "Sugar Level" for g in db.list_modifier_groups())
    mgr.close()
    window.close()


# =====================================================================
# 4. ORDER TYPES, RECEIPTS (EN LTR / AR RTL) & SNAPSHOT IMMUTABILITY
# =====================================================================


def test_order_types_bilingual_receipts_and_historical_snapshot_immutability(
    qapp, isolated_data_dir: Path, monkeypatch
):
    db = Database(get_database_path())
    saved_cfg, admin, cashier = _setup_owner_and_cashier(db)
    window = MainWindow(db, qapp)
    window.config = saved_cfg
    window._apply_branding()
    window.login_user(cashier)
    monkeypatch.setattr("cafe_pos.ui.main_window.ReceiptDialog.exec", lambda self: 1)

    products = db.list_products()
    latte, _esp, size_grp, extras_grp = _seed_sample_modifiers(db, products, admin)
    large_opt = next(o for o in size_grp.options if o.name_en == "Large")
    oat_opt = next(o for o in extras_grp.options if o.name_en == "Oat Milk")

    reg = window.register_view
    # Direct Delivery checkout with optional customer details
    reg.set_order_type(ORDER_TYPE_DELIVERY)
    reg.cust_name_edit.setText("Omar Hassan")
    reg.cust_phone_edit.setText("+20 111 222 3333")
    reg.deliv_addr_edit.setText("88 Zamalek St")

    # Add customized latte directly into cart cleanly using build_cart_line
    cust_dlg = ProductCustomizeDialog(saved_cfg, latte, db.get_product_modifiers(latte.id))
    cust_dlg._option_buttons[large_opt.id].click()
    cust_dlg._option_buttons[oat_opt.id].click()
    cust_dlg._on_confirm()
    line = cust_dlg.build_cart_line(quantity=2)
    reg.cart[line.cart_key] = line
    reg.refresh_cart(animate_cards=False)
    cust_dlg.close()

    expected_unit = latte.price_minor + 200 + 125
    assert reg.cart_totals_minor() == (2, expected_unit * 2)

    seen_badges: list[str] = []

    def _schedule_confirm_cash(expected_badge: str | None = None) -> None:
        def _confirm_cash(attempt: int = 0) -> None:
            dlg = qapp.activeModalWidget()
            if isinstance(dlg, CheckoutPaymentDialog):
                try:
                    seen_badges.append(dlg.order_type_badge.text())
                    if expected_badge is not None:
                        assert expected_badge in dlg.order_type_badge.text()
                    dlg.cash_edit.setText("50.00")
                    dlg.confirm_btn.click()
                except Exception:
                    dlg.reject()
                    raise
            elif attempt < 30:
                QTimer.singleShot(10, lambda: _confirm_cash(attempt + 1))

        QTimer.singleShot(0, _confirm_cash)

    _schedule_confirm_cash("Delivery")
    reg.trigger_checkout()
    assert any("Delivery" in b for b in seen_badges)

    orders = db.list_orders(actor=cashier)
    assert len(orders) == 1
    assert orders[0].order_type == ORDER_TYPE_DELIVERY
    assert orders[0].customer_name == "Omar Hassan"

    # Also test Dine-in with empty table and Takeaway (optional fields never required;
    # leftover table/delivery inputs are NOT attached to Takeaway orders)
    reg.set_order_type(ORDER_TYPE_DINE_IN)
    reg.table_edit.setText("T-09")
    reg.set_order_type(ORDER_TYPE_TAKEAWAY)
    croissant = next(p for p in products if "Croissant" in p.name)
    reg.add_product_to_cart(croissant)
    _schedule_confirm_cash("Takeaway")
    reg.trigger_checkout()
    takeaway_order = db.get_order(db.list_orders(actor=cashier)[0].id, actor=cashier)
    assert takeaway_order is not None
    assert takeaway_order.order_type == ORDER_TYPE_TAKEAWAY
    assert takeaway_order.table_number == ""

    # Delivery checkout with ALL customer fields empty succeeds seamlessly
    empty_deliv_order = db.create_order_with_payment(
        [CartLine(product_id=croissant.id, name=croissant.name, unit_price_minor=croissant.price_minor, quantity=1)],
        payment_method="CASH",
        cash_received_minor=1000,
        order_type=ORDER_TYPE_DELIVERY,
        actor=cashier,
    )
    assert empty_deliv_order.order_type == ORDER_TYPE_DELIVERY
    assert empty_deliv_order.customer_name == ""

    # Verify Sales History inspector shows order type for Delivery order
    window.switch_view(2)
    sales = window.sales_view
    # Find row for ORD-1001
    for r in range(sales.table.rowCount()):
        if sales.table.item(r, 0).text() == "ORD-1001":
            sales.table.selectRow(r)
            break
    assert "Delivery" in sales.meta_left_lbl.text()

    # SNAPSHOT IMMUTABILITY: Admin renames/re-prices modifier option, archives group, changes product price
    orig_order_id = orders[0].id
    db.update_product(latte.id, "Latte Renamed", latte.category, 9999, actor=admin)
    db.update_modifier_group(
        size_grp.id,
        "Size V2",
        name_ar="مقاس جديد",
        selection_mode=MODIFIER_MODE_SINGLE,
        is_required=True,
        options=[
            {"id": o.id, "name_en": f"{o.name_en} V2", "name_ar": o.name_ar, "price_delta_minor": 999}
            for o in size_grp.options
        ],
        actor=admin,
    )
    db.archive_modifier_group(size_grp.id, archived=True, actor=admin)

    loaded = db.get_order(orig_order_id, actor=cashier)
    assert loaded is not None
    assert loaded.items[0].product_name != "Latte Renamed"
    assert loaded.items[0].unit_price_minor == expected_unit
    assert loaded.items[0].modifiers[0].option_name_en == "Large"
    assert loaded.items[0].modifiers[0].option_name_ar == "كبير"
    assert loaded.items[0].modifiers[0].price_delta_minor == 200
    assert loaded.items[0].modifiers[1].option_name_en == "Oat Milk"
    assert loaded.items[0].modifiers[1].price_delta_minor == 125

    # Bilingual EN LTR + AR-EG RTL receipts contain order type, optional delivery fields & modifier snapshots
    txt_en = build_receipt_plain_text(loaded, saved_cfg, lang="en")
    assert "Delivery" in txt_en
    assert "Omar Hassan" in txt_en
    assert "88 Zamalek St" in txt_en
    assert "Large" in txt_en
    assert "Oat Milk" in txt_en

    txt_ar = build_receipt_plain_text(loaded, saved_cfg, lang="ar-EG")
    assert "دليفري" in txt_ar
    assert "كبير" in txt_ar
    assert "حليب شوفان" in txt_ar

    doc_ar = build_receipt_document(loaded, saved_cfg, lang="ar-EG")
    html_ar = doc_ar.toHtml()
    assert "دليفري" in html_ar
    assert "حليب شوفان" in html_ar
    window.close()


# =====================================================================
# 5. HOLD & RESUME ORDERS + DOUBLE-COMPLETION GUARD
# =====================================================================


def test_hold_resume_edit_cancel_and_atomic_completion_protection(
    qapp, isolated_data_dir: Path, monkeypatch
):
    db = Database(get_database_path())
    saved_cfg, admin, cashier = _setup_owner_and_cashier(db)
    window = MainWindow(db, qapp)
    window.config = saved_cfg
    window._apply_branding()
    window.login_user(cashier)
    monkeypatch.setattr("cafe_pos.ui.main_window.ReceiptDialog.exec", lambda self: 1)

    products = db.list_products()
    latte, _esp, size_grp, _extras = _seed_sample_modifiers(db, products, admin)
    croissant = next(p for p in products if "Croissant" in p.name)
    med_opt = next(o for o in size_grp.options if o.name_en == "Medium")

    reg = window.register_view
    assert db.get_order_count() == 0
    assert "0" in reg.held_orders_btn.text()

    # Build cart on Register with Dine-in + Table + customized Latte + Croissant -> Hold
    reg.set_order_type(ORDER_TYPE_DINE_IN)
    reg.table_edit.setText("B-07")
    cust_dlg = ProductCustomizeDialog(saved_cfg, latte, db.get_product_modifiers(latte.id))
    cust_dlg._option_buttons[med_opt.id].click()
    cust_dlg._on_confirm()
    line1 = cust_dlg.build_cart_line(quantity=1)
    cust_dlg.close()

    reg.cart[line1.cart_key] = line1
    reg.add_product_to_cart(croissant)
    expected_sub_1 = line1.line_total_minor + croissant.price_minor
    assert reg.cart_totals_minor() == (2, expected_sub_1)

    reg._hold_current_order()

    # Cart cleared; held count updated; completed sales/analytics completely unaffected
    assert len(reg.cart) == 0
    assert db.count_held_orders() == 1
    assert "1" in reg.held_orders_btn.text()
    assert db.get_order_count() == 0
    assert db.list_orders(actor=admin) == []
    assert db.get_sales_analytics("all", actor=admin).order_count == 0

    held_list = db.list_held_orders(actor=cashier)
    assert len(held_list) == 1
    held = held_list[0]
    assert held.status == HELD_STATUS_HELD
    assert held.order_type == ORDER_TYPE_DINE_IN
    assert held.table_number == "B-07"
    assert held.subtotal_minor == expected_sub_1

    # Search inside HeldOrdersDialog
    held_dlg = HeldOrdersDialog(db, saved_cfg, has_active_cart=False, parent=reg)
    assert len(held_dlg._held_orders) == 1
    held_dlg.search_input.setText("nonexistent-xyz")
    assert held_dlg.stack.currentIndex() == 0  # Empty state shown
    held_dlg.search_input.setText("B-07")
    assert held_dlg.stack.currentIndex() == 1
    assert len(held_dlg._held_orders) == 1
    held_dlg._resume_held(held_dlg._held_orders[0])
    assert held_dlg.result() == QDialog.Accepted
    assert held_dlg.resumed_order is not None
    reg.resume_held_order(held_dlg.resumed_order)
    held_dlg.close()

    # Verify full restoration: order type, table, items, modifiers, and editing badge
    assert reg._order_type == ORDER_TYPE_DINE_IN
    assert reg.table_edit.text() == "B-07"
    assert reg.held_editing_badge.isHidden() is False
    assert held.hold_number in reg.held_editing_badge.text()
    assert reg.cart_totals_minor() == (2, expected_sub_1)

    # Modify resumed ticket (add extra croissant & switch to Delivery) and re-hold -> updates same ticket
    reg.add_product_to_cart(croissant)
    reg.set_order_type(ORDER_TYPE_DELIVERY)
    reg.cust_name_edit.setText("Zaid")
    reg._hold_current_order()

    assert db.count_held_orders() == 1
    held_reloaded = db.get_held_order(held.id, actor=cashier)
    assert held_reloaded is not None
    assert held_reloaded.hold_number == held.hold_number
    assert held_reloaded.order_type == ORDER_TYPE_DELIVERY
    assert held_reloaded.customer_name == "Zaid"
    assert held_reloaded.item_count == 3

    # Resume and complete payment
    reg.resume_held_order(held_reloaded)

    def _pay_held(attempt: int = 0) -> None:
        dlg = qapp.activeModalWidget()
        if isinstance(dlg, CheckoutPaymentDialog):
            try:
                dlg.cash_edit.setText("50.00")
                dlg.confirm_btn.click()
            except Exception:
                dlg.reject()
                raise
        elif attempt < 30:
            QTimer.singleShot(10, lambda: _pay_held(attempt + 1))

    QTimer.singleShot(0, _pay_held)
    reg.trigger_checkout()

    assert db.count_held_orders() == 0
    assert db.get_order_count() == 1
    completed_held = db.get_held_order(held.id, actor=cashier)
    assert completed_held is not None
    assert completed_held.status == HELD_STATUS_COMPLETED
    assert completed_held.completed_order_id == db.list_orders(actor=cashier)[0].id

    # Duplicate completion of the same held_order_id MUST fail atomically without creating a 2nd order
    with pytest.raises(ValueError):
        db.create_order_with_payment(
            [CartLine(product_id=croissant.id, name=croissant.name, unit_price_minor=croissant.price_minor, quantity=1)],
            payment_method="CASH",
            cash_received_minor=1000,
            held_order_id=held.id,
            actor=cashier,
        )
    assert db.get_order_count() == 1

    # Hold a second order and cancel it via HeldOrdersDialog confirmation
    reg.add_product_to_cart(croissant)
    reg._hold_current_order()
    assert db.count_held_orders() == 1
    held2 = db.list_held_orders(actor=cashier)[0]

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)
    held_dlg2 = HeldOrdersDialog(db, saved_cfg, has_active_cart=False, parent=reg)
    held_dlg2._confirm_cancel_held(held2)
    assert db.count_held_orders() == 0
    cancelled = db.get_held_order(held2.id, actor=cashier)
    assert cancelled is not None and cancelled.status == HELD_STATUS_CANCELLED
    assert db.get_order_count() == 1
    held_dlg2.close()
    window.close()


# =====================================================================
# 6. MODIFIER MANAGER ASSIGNED PRODUCTS CHECKLIST & REGISTER E2E FLOW
# =====================================================================


def test_modifier_manager_product_assignment_row_clicks_validation_and_register_flow(
    qapp, isolated_data_dir: Path, monkeypatch
):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionViewItem

    from cafe_pos.i18n import set_language, tr
    from cafe_pos.theme import apply_theme

    set_language("en")
    db = Database(get_database_path())
    saved_cfg, admin, _cashier = _setup_owner_and_cashier(db)
    apply_theme(qapp, saved_cfg, lang="en")

    # Ensure existing group + option recipe + prices stay untouched after editing another group
    products = db.list_products()
    assert len(products) >= 4
    latte, _esp, existing_size, _extras = _seed_sample_modifiers(db, products, admin)
    ing = db.create_ingredient("Test Milk", "حليب", unit="ml", stock_qty_milli=5000, actor=admin)
    large_opt = next(o for o in existing_size.options if o.name_en == "Large")
    db.set_modifier_option_recipe(large_opt.id, [(ing.id, 150)], actor=admin)

    warnings: list[str] = []
    infos: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _p, _t, msg: warnings.append(str(msg)) or QMessageBox.Ok,
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _p, _t, msg: infos.append(str(msg)) or QMessageBox.Ok,
    )

    window = MainWindow(db, qapp)
    window.config = saved_cfg
    window._apply_branding()
    window.login_user(admin)

    mgr = ModifierManagerDialog(db, saved_cfg, parent=window)
    mgr.show()
    qapp.processEvents()

    # 1. Validation checks before creating new group
    mgr._start_new_group()
    qapp.processEvents()
    assert "0 of" in mgr.assigned_count_badge.text()

    # Missing group name
    mgr.save_group_btn.click()
    assert len(warnings) == 1
    assert mgr.validation_lbl.isVisible()

    # Valid name, option row has extra price without option name
    mgr.name_en_edit.setText("Syrups")
    mgr.name_ar_edit.setText("نكهات")
    mgr.options_table.cellWidget(0, 2).setText("1.50")
    mgr.save_group_btn.click()
    assert len(warnings) == 2
    assert mgr.validation_lbl.isVisible()

    # Valid option name + invalid price text
    mgr.options_table.cellWidget(0, 0).setText("Vanilla Syrup")
    mgr.options_table.cellWidget(0, 2).setText("abc")
    mgr.save_group_btn.click()
    assert len(warnings) == 3

    # Required multi-select with min > max
    mgr.options_table.cellWidget(0, 2).setText("1.25")
    mgr.mode_combo.setCurrentIndex(1)  # Multi
    mgr.min_spin.setValue(3)
    mgr.max_spin.setValue(2)
    mgr.save_group_btn.click()
    assert len(warnings) == 4

    # Reset rules to valid multi-select with multiple options & prices
    mgr.min_spin.setValue(0)
    mgr.max_spin.setValue(2)
    mgr.required_chk.setChecked(True)
    # Mark only option disabled -> should block required group with 0 active options
    opt0_chk = mgr.options_table.cellWidget(0, 3).findChild(QCheckBox)
    assert opt0_chk is not None
    opt0_chk.setChecked(False)
    mgr.save_group_btn.click()
    assert len(warnings) == 5

    # Restore active option + add two more options
    opt0_chk.setChecked(True)
    mgr.required_chk.setChecked(False)
    mgr.add_opt_btn.click()
    mgr.options_table.cellWidget(1, 0).setText("Caramel Drizzle")
    mgr.options_table.cellWidget(1, 1).setText("كراميل")
    mgr.options_table.cellWidget(1, 2).setText("2.00")
    mgr.add_opt_btn.click()
    mgr.options_table.cellWidget(2, 0).setText("No Whipped Cream")
    mgr.options_table.cellWidget(2, 2).setText("0.00")

    # 2. Assigned Products click testing:
    # Click on row text/whitespace (right of checkbox indicator) AND directly inside indicator rect
    lv = mgr.products_list
    vp = lv.viewport()
    assert lv.count() == len(products)

    # Find two clean products not yet in products[:2] if possible
    target_p1 = products[2]
    target_p2 = products[3]
    row1 = next(i for i in range(lv.count()) if lv.item(i).data(Qt.UserRole) == target_p1.id)
    row2 = next(i for i in range(lv.count()) if lv.item(i).data(Qt.UserRole) == target_p2.id)

    lv.scrollToItem(lv.item(row1))
    qapp.processEvents()
    r1 = lv.visualRect(lv.model().index(row1, 0))
    # Click far right area of row -> checks row1 cleanly
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, QPoint(r1.right() - 24, r1.center().y()))
    qapp.processEvents()
    assert lv.item(row1).checkState() == Qt.Checked
    assert lv.item(row1).font().bold() is True

    # Click directly inside indicator rect on row1 -> unchecks cleanly (no double-toggle)
    opt_view = QStyleOptionViewItem()
    opt_view.initFrom(lv)
    opt_view.rect = r1
    opt_view.features |= QStyleOptionViewItem.HasCheckIndicator
    ind1 = lv.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator, opt_view, lv)
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, ind1.center())
    qapp.processEvents()
    assert lv.item(row1).checkState() == Qt.Unchecked
    assert lv.item(row1).font().bold() is False

    # Re-check row1 via indicator rect, and check row2 via center text area
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, ind1.center())
    qapp.processEvents()
    assert lv.item(row1).checkState() == Qt.Checked

    lv.scrollToItem(lv.item(row2))
    qapp.processEvents()
    r2 = lv.visualRect(lv.model().index(row2, 0))
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, r2.center())
    qapp.processEvents()
    assert lv.item(row2).checkState() == Qt.Checked
    assert mgr.assigned_count_badge.text() == tr(
        "modifiers.assigned_count", count=2, total=len(products)
    )

    # Verify painted pixels actually render Boutique green checkmark on checked row and white box on unchecked row
    lv.scrollToItem(lv.item(0))
    qapp.processEvents()
    snap = lv.viewport().grab().toImage()
    r_checked = lv.visualRect(lv.model().index(row2, 0))
    if r_checked.intersects(lv.viewport().rect()):
        has_green = any(
            snap.pixelColor(x, y).name().lower() == "#2e6b45"
            for x in range(r_checked.left() + 6, r_checked.left() + 36)
            for y in range(r_checked.top() + 4, r_checked.bottom() - 4)
        )
        assert has_green
    r_unchecked = lv.visualRect(lv.model().index(0, 0))
    if r_unchecked.intersects(lv.viewport().rect()):
        has_green_unchecked = any(
            snap.pixelColor(x, y).name().lower() == "#2e6b45"
            for x in range(r_unchecked.left() + 6, r_unchecked.left() + 36)
            for y in range(r_unchecked.top() + 4, r_unchecked.bottom() - 4)
        )
        assert not has_green_unchecked

    # Save new group & close dialog
    mgr.save_group_btn.click()
    assert len(infos) == 1
    assert mgr.validation_lbl.isHidden()
    mgr.close()

    # 3. Reopen fresh ModifierManagerDialog and verify persistence of options & checked rows
    mgr2 = ModifierManagerDialog(db, saved_cfg, parent=window)
    mgr2.show()
    qapp.processEvents()
    syrup_row = next(
        i for i, g in enumerate(mgr2._groups) if g.name_en == "Syrups"
    )
    mgr2.group_list.setCurrentRow(syrup_row)
    qapp.processEvents()
    assert mgr2.assigned_count_badge.text() == tr(
        "modifiers.assigned_count", count=2, total=len(products)
    )
    checked_ids = set(mgr2._collect_assigned_product_ids())
    assert checked_ids == {target_p1.id, target_p2.id}
    assert mgr2.options_table.rowCount() == 3

    # 4. Edit existing group assignments: uncheck target_p1, check products[1], save & reopen
    mgr2.products_list.scrollToItem(mgr2.products_list.item(row1))
    qapp.processEvents()
    r1_again = mgr2.products_list.visualRect(mgr2.products_list.model().index(row1, 0))
    QTest.mouseClick(
        mgr2.products_list.viewport(), Qt.LeftButton, Qt.NoModifier, r1_again.center()
    )
    qapp.processEvents()
    alt_row = next(
        i for i in range(mgr2.products_list.count())
        if mgr2.products_list.item(i).data(Qt.UserRole) == products[1].id
    )
    mgr2.products_list.scrollToItem(mgr2.products_list.item(alt_row))
    qapp.processEvents()
    r_alt = mgr2.products_list.visualRect(mgr2.products_list.model().index(alt_row, 0))
    QTest.mouseClick(
        mgr2.products_list.viewport(), Qt.LeftButton, Qt.NoModifier, r_alt.center()
    )
    qapp.processEvents()
    mgr2.save_group_btn.click()
    mgr2.close()

    # Verify in fresh dialog + verify pre-existing group/recipe/prices remained intact
    mgr3 = ModifierManagerDialog(db, saved_cfg, parent=window)
    syrup_grp = next(g for g in mgr3._groups if g.name_en == "Syrups")
    assert set(syrup_grp.assigned_product_ids) == {products[1].id, target_p2.id}
    assert [o.price_delta_minor for o in syrup_grp.options] == [125, 200, 0]

    reloaded_size = db.get_modifier_group(existing_size.id)
    assert reloaded_size is not None
    assert set(reloaded_size.assigned_product_ids) == set(existing_size.assigned_product_ids)
    assert [
        (r.ingredient_id, r.qty_milli)
        for r in db.get_modifier_option_recipe(large_opt.id)
    ] == [(ing.id, 150)]
    mgr3.close()

    # 5. RTL Arabic mode check on assigned row toggling & count badge
    set_language("ar-EG")
    apply_theme(qapp, saved_cfg, lang="ar-EG")
    mgr_ar = ModifierManagerDialog(db, saved_cfg, parent=window)
    mgr_ar.show()
    qapp.processEvents()
    syrup_row_ar = next(i for i, g in enumerate(mgr_ar._groups) if g.name_en == "Syrups")
    mgr_ar.group_list.setCurrentRow(syrup_row_ar)
    qapp.processEvents()
    before_ar_state = mgr_ar.products_list.item(0).checkState()
    r0_ar = mgr_ar.products_list.visualRect(mgr_ar.products_list.model().index(0, 0))
    QTest.mouseClick(mgr_ar.products_list.viewport(), Qt.LeftButton, Qt.NoModifier, r0_ar.center())
    qapp.processEvents()
    assert mgr_ar.products_list.item(0).checkState() != before_ar_state
    mgr_ar.select_all_products_btn.click()
    assert len(mgr_ar._collect_assigned_product_ids()) == len(products)
    mgr_ar.clear_products_btn.click()
    assert mgr_ar._collect_assigned_product_ids() == []
    mgr_ar.close()

    # 6. End-to-end Register flow: assigned product opens ProductCustomizeDialog with new group
    set_language("en")
    apply_theme(qapp, saved_cfg, lang="en")
    window.register_view.refresh_catalog()
    qapp.processEvents()

    opened_dialogs: list[tuple[str, list[str]]] = []
    orig_exec = ProductCustomizeDialog.exec

    def _capture_and_pick(self_dlg):
        opened_dialogs.append(
            (
                self_dlg.product.name_en or self_dlg.product.name,
                [g.name_en for g in self_dlg.groups],
            )
        )
        # Pick Vanilla Syrup (+1.25) and Caramel Drizzle (+2.00)
        for grp in self_dlg.groups:
            if grp.name_en == "Syrups":
                for opt in grp.active_options:
                    if opt.name_en in ("Vanilla Syrup", "Caramel Drizzle"):
                        self_dlg._option_buttons[opt.id].click()
        self_dlg._on_confirm()
        return QDialog.Accepted

    monkeypatch.setattr(ProductCustomizeDialog, "exec", _capture_and_pick)
    window.register_view.add_product_to_cart(target_p2)
    assert len(opened_dialogs) == 1
    assert "Syrups" in opened_dialogs[0][1]
    added_line = next(iter(window.register_view.cart.values()))
    assert added_line.product_id == target_p2.id
    assert added_line.unit_price_minor == target_p2.price_minor + 125 + 200

    # Unassigned product (products[4]) adds directly without opening ProductCustomizeDialog
    unassigned_prod = products[4]
    if not db.get_product_modifiers(unassigned_prod.id):
        window.register_view.add_product_to_cart(unassigned_prod)
        assert len(opened_dialogs) == 1
        assert unassigned_prod.id in window.register_view.cart

    monkeypatch.setattr(ProductCustomizeDialog, "exec", orig_exec)
    window.close()
