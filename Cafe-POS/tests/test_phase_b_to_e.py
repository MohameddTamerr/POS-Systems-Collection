from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QDate, Qt
from PySide6.QtWidgets import QMessageBox, QPushButton

from cafe_pos.auth import AuthorizationError, ROLE_CASHIER, UserAccount
from cafe_pos.backups import create_backup_archive, inspect_and_validate_backup, restore_backup_archive
from cafe_pos.config import BusinessConfig
from cafe_pos.db import (
    CartLine,
    CartModifierSelection,
    DISCOUNT_TYPE_FIXED,
    DISCOUNT_TYPE_PERCENT,
    Database,
    ORDER_TYPE_TAKEAWAY,
)
from cafe_pos.i18n import set_language
from cafe_pos.paths import get_backups_dir, get_database_path
from cafe_pos.payments import METHOD_CARD, METHOD_CASH
from cafe_pos.receipts import build_receipt_document, build_receipt_plain_text
from cafe_pos.ui.inventory_view import IngredientEditorDialog, OptionRecipeDialog, StockAdjustDialog
from cafe_pos.ui.main_window import MainWindow
from cafe_pos.ui.modifier_manager_dialog import ModifierManagerDialog
from cafe_pos.ui.payment_dialog import CheckoutPaymentDialog
from cafe_pos.ui.product_editor_dialog import ProductEditorDialog
from cafe_pos.ui.refund_dialog import RefundOrderDialog
from cafe_pos.ui.shift_dialog import ShiftManagerDialog


def _seed_users_and_store(isolated_data_dir: Path) -> tuple[Database, UserAccount, UserAccount]:
    db = Database(get_database_path())
    cfg = BusinessConfig(
        business_name="MoCoffee Boutique",
        accent_color="#005B37",
        currency="USD",
        setup_completed=True,
        reduced_motion=True,
    )
    _saved_cfg, admin = db.initialize_owner_and_business(
        business_cfg=cfg,
        admin_username="admin_owner",
        admin_password="AdminStrong!2026",
        admin_display_name="Owner Admin",
    )
    cashier = db.create_user(
        "barista1",
        "CashierPass!2026",
        role=ROLE_CASHIER,
        display_name="Sara Barista",
        actor=admin,
    )
    return db, admin, cashier


def test_settings_sections_unsaved_guard_and_test_print(
    isolated_data_dir: Path, qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, admin, _cashier = _seed_users_and_store(isolated_data_dir)
    win = MainWindow(db, qapp)
    win.show()
    win.login_user(admin)
    win.switch_view(5)
    qapp.processEvents()

    sv = win.settings_view
    assert len(sv.section_buttons) == 8
    for idx in range(8):
        sv.switch_section(idx)
        assert sv.sections_stack.currentIndex() == idx

    assert sv.has_unsaved_changes() is False
    sv.name_edit.setText("MoCoffee Modified")
    assert sv.has_unsaved_changes() is True

    # Simulate choosing "Stay on Settings" when trying to navigate away
    monkeypatch.setattr(sv, "prompt_unsaved_changes", lambda: "cancel")
    win.switch_view(0)
    assert win.stack.currentIndex() == 5

    # Simulate choosing "Discard Changes" -> navigates cleanly & restores saved text
    def _discard() -> str:
        sv._discard_changes()
        return "discard"

    monkeypatch.setattr(sv, "prompt_unsaved_changes", _discard)
    win.switch_view(0)
    assert win.stack.currentIndex() == 0
    assert sv.has_unsaved_changes() is False
    assert sv.name_edit.text() == "MoCoffee Boutique"

    # Persist new Pro settings via SettingsView
    win.switch_view(5)
    sv.chk_allow_cashier_discounts.setChecked(True)
    sv.max_cashier_discount_spin.setValue(15)
    sv.chk_allow_cashier_refunds.setChecked(True)
    sv.chk_require_shift_for_sales.setChecked(True)
    idx_58 = sv.paper_width_combo.findData(58)
    assert idx_58 >= 0
    sv.paper_width_combo.setCurrentIndex(idx_58)
    sv.chk_auto_print_receipt.setChecked(True)
    sv.chk_show_biz_details_receipt.setChecked(False)
    sv.chk_show_cashier_receipt.setChecked(False)
    assert sv._save_settings() is True

    reloaded = db.get_settings()
    assert reloaded.allow_cashier_discounts is True
    assert reloaded.max_cashier_discount_pct == 15
    assert reloaded.allow_cashier_refunds is True
    assert reloaded.require_shift_for_sales is True
    assert reloaded.printer_paper_width_mm == 58
    assert reloaded.auto_print_receipt is True
    assert reloaded.show_business_details_on_receipt is False
    assert reloaded.show_cashier_on_receipt is False

    # Test print button calls thermal print helper without raising
    called: list[int | None] = []
    monkeypatch.setattr(
        "cafe_pos.ui.settings_view.print_test_thermal_receipt",
        lambda cfg, **kw: called.append(kw.get("paper_width_mm")) or (True, "MockThermal80"),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: QMessageBox.Ok)
    sv._run_test_print()
    assert called == [58]
    win.close()


def test_cashier_shift_lifecycle_require_shift_gate_and_csv(
    isolated_data_dir: Path, qapp, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db, admin, cashier = _seed_users_and_store(isolated_data_dir)
    cfg = db.get_settings()
    cfg.require_shift_for_sales = True
    cfg.require_opening_cash = True
    cfg = db.save_settings(cfg, actor=admin)

    prod_card = db.add_product("Card Blend", "Espresso", 2000, actor=admin)
    prod_cash = db.add_product("Cash Blend", "Espresso", 3000, actor=admin)
    win = MainWindow(db, qapp)
    win.login_user(cashier)
    qapp.processEvents()

    # Without an open shift, checkout prompts and aborts when user declines opening a shift
    win.register_view.add_product_to_cart(prod_card)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.No)
    win.register_view.trigger_checkout()
    assert db.get_order_count() == 0

    # Open shift via ShiftManagerDialog with $100.00 float + Cash In $25 + Cash Out $10
    sdlg = ShiftManagerDialog(db, cfg, current_user=cashier)
    sdlg.opening_cash_edit.setText("100.00")
    sdlg.open_notes_edit.setText("Morning float")
    sdlg._on_open_shift()
    active = db.get_active_shift(cashier.id, actor=cashier)
    assert active is not None
    assert active.opening_cash_minor == 10000

    # Record Cash In $25 and Cash Out $10
    in_idx = sdlg.mv_type_combo.findData("CASH_IN")
    sdlg.mv_type_combo.setCurrentIndex(in_idx)
    sdlg.mv_amount_edit.setText("25.00")
    sdlg.mv_reason_edit.setText("Extra coins")
    sdlg._on_record_movement()

    out_idx = sdlg.mv_type_combo.findData("CASH_OUT")
    sdlg.mv_type_combo.setCurrentIndex(out_idx)
    sdlg.mv_amount_edit.setText("10.00")
    sdlg.mv_reason_edit.setText("Milk run")
    sdlg._on_record_movement()

    # Record 1 CARD sale ($20.00) and 1 CASH sale ($30.00) -> CARD excluded from drawer cash!
    line_card = CartLine(
        product_id=prod_card.id,
        name=prod_card.name,
        unit_price_minor=2000,
        quantity=1,
        base_unit_price_minor=2000,
    )
    db.create_order_with_payment(
        [line_card],
        payment_method=METHOD_CARD,
        confirmed_external=True,
        external_reference="CARD-101",
        actor=cashier,
    )
    line_cash = CartLine(
        product_id=prod_cash.id,
        name=prod_cash.name,
        unit_price_minor=3000,
        quantity=1,
        base_unit_price_minor=3000,
    )
    db.create_order_with_payment(
        [line_cash],
        payment_method=METHOD_CASH,
        cash_received_minor=3000,
        actor=cashier,
    )

    sdlg.refresh()
    refreshed = sdlg._active_shift
    assert refreshed is not None
    assert refreshed.cash_sales_minor == 3000
    assert refreshed.digital_sales_minor == 2000
    # Expected = 100.00 + 30.00 + 25.00 - 10.00 = 145.00 (14500 minor)
    assert refreshed.expected_cash_minor == 14500

    # Close shift counting $140.00 -> discrepancy -500 (-$5.00)
    sdlg.counted_cash_edit.setText("140.00")
    assert sdlg.discrepancy_val_lbl.text()
    sdlg._on_close_shift()
    closed = db.get_shift(refreshed.id, actor=cashier)
    assert closed is not None
    assert closed.status == "CLOSED"
    assert closed.discrepancy_minor == -500

    # Export CSV
    csv_out = tmp_path / "shift_export.csv"
    db.export_shift_csv(closed.id, csv_out, actor=cashier)
    rows = list(csv.reader(csv_out.open(encoding="utf-8")))
    assert len(rows) >= 2
    assert any("145" in col or "145.00" in col for r in rows for col in r)
    sdlg.close()
    win.close()


def test_register_discounts_cashier_cap_and_split_payments(
    isolated_data_dir: Path, qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, admin, cashier = _seed_users_and_store(isolated_data_dir)
    cfg = db.get_settings()
    cfg.allow_cashier_discounts = True
    cfg.max_cashier_discount_pct = 10
    cfg = db.save_settings(cfg, actor=admin)

    espresso = db.add_product("Double House Blend", "Espresso", 2000, actor=admin)
    win = MainWindow(db, qapp)
    win.login_user(cashier)
    qapp.processEvents()

    rv = win.register_view
    rv.add_product_to_cart(espresso)

    # Exceeding 10% cashier cap shows validation error & blocks checkout
    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, msg: warned.append(str(msg)) or QMessageBox.Ok)
    pct_idx = rv.discount_type_combo.findData(DISCOUNT_TYPE_PERCENT)
    rv.discount_type_combo.setCurrentIndex(pct_idx)
    rv.discount_val_edit.setText("25")
    dtype, dval, _dlbl, dminor, err = rv.cart_discount_details()
    assert err != ""
    assert dminor == 500
    rv.trigger_checkout()
    assert len(warned) == 1
    assert db.get_order_count() == 0

    # Valid 10% cashier discount ($2.00 off $20.00 => $18.00 net)
    rv.discount_val_edit.setText("10")
    rv.discount_label_edit.setText("VIP")
    dtype, dval, dlbl, dminor, err = rv.cart_discount_details()
    assert err == ""
    assert dtype == DISCOUNT_TYPE_PERCENT
    assert dval == 10
    assert dminor == 200
    assert dlbl == "VIP"

    # Split Payment checkout via CheckoutPaymentDialog ($10.00 CASH + $8.00 CARD)
    dlg = CheckoutPaymentDialog(
        db,
        cfg,
        list(rv.cart.values()),
        discount_type=dtype,
        discount_value=dval,
        discount_label=dlbl,
        discount_minor=dminor,
    )
    dlg.set_split_mode(True)
    assert dlg._total_minor == 1800

    cash_i = dlg.split_method_combo.findData(METHOD_CASH)
    dlg.split_method_combo.setCurrentIndex(cash_i)
    dlg.split_amount_edit.setText("10.00")
    dlg.split_cash_received_edit.setText("15.00")
    dlg._on_add_split_leg()
    assert len(dlg._split_legs) == 1

    card_i = dlg.split_method_combo.findData(METHOD_CARD)
    dlg.split_method_combo.setCurrentIndex(card_i)
    dlg.split_amount_edit.setText("8.00")
    dlg.split_ref_edit.setText("POS-991")
    dlg.split_confirm_chk.setChecked(True)
    dlg._on_add_split_leg()
    assert len(dlg._split_legs) == 2

    dlg._on_confirm()
    assert dlg.result_order is not None
    order = dlg.result_order
    assert order.subtotal_minor == 2000
    assert order.discount_minor == 200
    assert order.total_minor == 1800
    assert order.discount_label == "VIP"
    assert len(order.payments) == 2
    assert sorted(p.payment_amount_minor for p in order.payments) == [800, 1000]

    # Cancelling split dialog preserves cart
    rv.cart.clear()
    rv.add_product_to_cart(espresso)
    dlg_cancel = CheckoutPaymentDialog(db, cfg, list(rv.cart.values()))
    dlg_cancel.set_split_mode(True)
    dlg_cancel.reject()
    assert len(rv.cart) == 1
    win.close()


def test_partial_and_full_refund_ui_stock_actions_and_rbac(
    isolated_data_dir: Path, qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, admin, cashier = _seed_users_and_store(isolated_data_dir)
    cfg = db.get_settings()
    cfg.allow_cashier_refunds = False
    cfg = db.save_settings(cfg, actor=admin)

    beans = db.create_ingredient(
        "House Beans",
        "بن",
        unit="g",
        stock_qty_milli=1_000_000,  # 1000g
        min_threshold_milli=100_000,
        cost_per_unit_minor=1,
        actor=admin,
    )
    latte = db.add_product("Pro Latte", "Espresso", 1000, actor=admin)
    db.set_product_recipe(latte.id, [(beans.id, 18_000)], actor=admin)  # 18g per cup

    line = CartLine(
        product_id=latte.id,
        name=latte.name,
        unit_price_minor=1000,
        quantity=2,
        base_unit_price_minor=1000,
    )
    order = db.create_order_with_payment(
        [line],
        payment_method=METHOD_CASH,
        cash_received_minor=2000,
        discount_type=DISCOUNT_TYPE_FIXED,
        discount_value=400,
        discount_minor=400,
        actor=admin,
    )
    # 2 * 18g = 36g deducted -> 964g remaining
    assert db.get_ingredient(beans.id).stock_qty_milli == 964_000

    # Cashier cannot refund when allow_cashier_refunds is False
    with pytest.raises(AuthorizationError):
        db.create_order_refund(
            order.id,
            [{"order_item_id": order.items[0].id, "quantity": 1, "stock_action": "NONE"}],
            refund_method="CASH",
            actor=cashier,
        )

    # Admin partially refunds 1x Latte with RETURNED stock (+18g => 982g), proportional $8.00
    db.set_session_actor(admin)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: QMessageBox.Ok)
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda _p, _t, msg: (_ for _ in ()).throw(AssertionError(f"Unexpected QMessageBox.critical: {msg}")),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _p, _t, msg: (_ for _ in ()).throw(AssertionError(f"Unexpected QMessageBox.warning: {msg}")),
    )
    rd1 = RefundOrderDialog(db, cfg, order)
    rd1._row_controls[0].qty_spin.setValue(1)
    ret_idx = rd1._row_controls[0].action_combo.findData("RETURNED")
    rd1._row_controls[0].action_combo.setCurrentIndex(ret_idx)
    rd1.reason_edit.setText("Wrong milk")
    assert rd1._estimated_refund_minor() == 800
    rd1._on_submit()
    assert rd1.refund_id is not None
    assert db.get_ingredient(beans.id).stock_qty_milli == 982_000

    partial_ord = db.get_order(order.id, actor=admin)
    assert partial_ord is not None
    assert partial_ord.refunded_total_minor == 800
    assert partial_ord.net_paid_minor == 800

    # Refund remaining 1x Latte as WASTE (stock stays 982g, not restored)
    rd2 = RefundOrderDialog(db, cfg, partial_ord)
    assert rd2._row_controls[0].remaining_qty == 1
    rd2._select_all_remaining()
    waste_idx = rd2._row_controls[0].action_combo.findData("WASTE")
    rd2._row_controls[0].action_combo.setCurrentIndex(waste_idx)
    rd2.reason_edit.setText("Dropped cup")
    rd2._on_submit()
    assert rd2.refund_id is not None
    assert db.get_ingredient(beans.id).stock_qty_milli == 982_000

    full_ord = db.get_order(order.id, actor=admin)
    assert full_ord is not None
    assert full_ord.refunded_total_minor == 1600
    assert full_ord.net_paid_minor == 0


def test_inventory_recipes_low_stock_alert_navigation_and_held_non_deduction(
    isolated_data_dir: Path, qapp
) -> None:
    db, admin, _cashier = _seed_users_and_store(isolated_data_dir)
    cfg = db.get_settings()

    oat = db.create_ingredient(
        "Oat Milk",
        "حليب شوفان",
        unit="ml",
        stock_qty_milli=150_000,  # 150ml <= 200ml -> low stock!
        min_threshold_milli=200_000,
        cost_per_unit_minor=2,
        actor=admin,
    )
    assert oat.is_low_stock is True

    win = MainWindow(db, qapp)
    win.login_user(admin)
    qapp.processEvents()

    # RegisterView displays Low Stock pill and clicking it opens ProductsView -> Inventory tab filtered
    rv = win.register_view
    rv.refresh_operational_badges()
    assert rv.low_stock_badge_btn.isHidden() is False
    rv.low_stock_badge_btn.click()
    assert win.stack.currentIndex() == 1
    assert win.products_view.view_stack.currentIndex() == 1
    assert win.products_view.inventory_view.low_only_chk.isChecked() is True

    # Create product + attach modifier group + recipes
    mocha = db.add_product("Oat Mocha", "Espresso", 550, actor=admin)
    db.set_product_recipe(mocha.id, [(oat.id, 30_000)], actor=admin)
    milk_grp = db.create_modifier_group(
        "Milk Choice",
        name_ar="نوع الحليب",
        selection_mode="SINGLE",
        is_required=False,
        options=[{"name_en": "Oat Upgrade", "name_ar": "حليب شوفان", "price_delta_minor": 150}],
        product_ids=[mocha.id],
        actor=admin,
    )
    opt_id = milk_grp.options[0].id
    db.set_modifier_option_recipe(opt_id, [(oat.id, 120_000)], actor=admin)  # 120ml per upgrade

    # ProductEditorDialog Recipe tab loads & exposes entries
    ped = ProductEditorDialog(cfg, db.list_categories(), product=mocha, db=db, is_admin=True)
    assert ped.recipe_editor is not None
    assert ped.recipe_editor.entries() == [(oat.id, 30_000)]

    # Held Order NEVER deducts inventory!
    held_line = CartLine(
        product_id=mocha.id,
        name=mocha.name,
        unit_price_minor=700,
        quantity=1,
        base_unit_price_minor=550,
        modifiers=(
            CartModifierSelection(
                group_id=milk_grp.id,
                option_id=opt_id,
                group_name="Milk Choice",
                option_name="Oat Upgrade",
                price_delta_minor=150,
            ),
        ),
    )
    db.create_or_update_held_order([held_line], order_type=ORDER_TYPE_TAKEAWAY, actor=admin)
    assert db.get_ingredient(oat.id).stock_qty_milli == 150_000

    # Manual restock +500ml via StockAdjustDialog -> 650ml, then completing sale deducts 30+120=150ml -> 500ml
    sad = StockAdjustDialog(db, cfg, db.get_ingredient(oat.id))
    sad.qty_edit.setText("500")
    sad._on_save()
    assert db.get_ingredient(oat.id).stock_qty_milli == 650_000

    db.create_order_with_payment(
        [held_line],
        payment_method=METHOD_CASH,
        cash_received_minor=700,
        actor=admin,
    )
    assert db.get_ingredient(oat.id).stock_qty_milli == 500_000
    ped.close()
    sad.close()
    win.close()


def test_expenses_interactive_dashboard_profit_gate_and_category_filter(
    isolated_data_dir: Path, qapp
) -> None:
    db, admin, _cashier = _seed_users_and_store(isolated_data_dir)
    win = MainWindow(db, qapp)
    win.login_user(admin)
    win.switch_view(3)
    qapp.processEvents()

    rep = win.reports_view
    # Record Operating Expense cleanly (separated from recipe COGS)
    db.create_expense(
        "RENT",
        "Monthly Rent",
        50000,
        date.today().isoformat(),
        notes="Invoice #1",
        actor=admin,
    )
    rep.switch_subview(1)
    assert rep.expenses_view.table.rowCount() == 1

    # Add an order WITHOUT recipe cost data -> Profit KPI must show gated "—" state, NEVER fake 100% margin
    uncosted = db.add_product("Uncosted Muffin", "Bakery", 400, actor=admin)
    db.create_order(
        [CartLine(product_id=uncosted.id, name=uncosted.name, unit_price_minor=400, quantity=1, base_unit_price_minor=400)],
        cash_received_minor=400,
        actor=admin,
    )
    rep.switch_subview(0)
    rep._set_period("today")
    qapp.processEvents()
    assert rep.kpi_profit.value_lbl.text() == "—"
    assert "4" in rep.kpi_revenue.value_lbl.text()
    assert "500" in rep.kpi_expenses.value_lbl.text()

    # Now verify costed-only slice shows reliable numeric Gross Profit & expenses remain separate
    costed_ing = db.create_ingredient(
        "Matcha Powder",
        unit="g",
        stock_qty_milli=100_000,
        cost_per_unit_minor=20,  # $0.20 per gram (20000 milli-cost = 20 minor * 10g = 200 minor = $2.00)
        actor=admin,
    )
    matcha = db.add_product("Premium Matcha", "Tea", 1000, actor=admin)
    db.set_product_recipe(matcha.id, [(costed_ing.id, 10_000)], actor=admin)
    db.create_order(
        [CartLine(product_id=matcha.id, name=matcha.name, unit_price_minor=1000, quantity=1, base_unit_price_minor=1000)],
        cash_received_minor=1000,
        actor=admin,
    )
    # Click category "Tea" filter chip via interactive chart signal -> isolates costed Tea order!
    rep.cat_chart_widget.category_clicked.emit("Tea")
    qapp.processEvents()
    assert rep.cat_chip_btn.isHidden() is False
    assert rep.kpi_profit.value_lbl.text() != "—"
    assert "8.00" in rep.kpi_profit.value_lbl.text()
    win.close()


def test_thermal_auto_print_resilience_and_v8_backup_restore(
    isolated_data_dir: Path, qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, admin, _cashier = _seed_users_and_store(isolated_data_dir)
    cfg = db.get_settings()
    cfg.auto_print_receipt = True
    cfg.printer_paper_width_mm = 58
    cfg = db.save_settings(cfg, actor=admin)

    # Create rich Schema v8 data (ingredient, recipe, shift, discounted order, refund, expense)
    milk = db.create_ingredient("Whole Milk", unit="ml", stock_qty_milli=2_000_000, cost_per_unit_minor=1, actor=admin)
    latte = db.list_products()[0]
    db.set_product_recipe(latte.id, [(milk.id, 200_000)], actor=admin)
    shift = db.open_shift(10000, actor=admin)
    exp = db.create_expense("UTILITIES", "Water Bill", 12000, date.today().isoformat(), actor=admin)

    win = MainWindow(db, qapp)
    win.login_user(admin)
    qapp.processEvents()

    # Auto-print printer failure must NOT crash order completion or skip ReceiptDialog
    receipt_exec_called: list[str] = []
    monkeypatch.setattr(
        "cafe_pos.ui.main_window.print_receipt_to_printer",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("Printer unplugged")),
    )
    monkeypatch.setattr(
        "cafe_pos.ui.main_window.ReceiptDialog.exec",
        lambda self: receipt_exec_called.append(self.order.order_number) or 1,
    )

    order = db.create_order_with_payment(
        [CartLine(product_id=latte.id, name=latte.name, unit_price_minor=500, quantity=2, base_unit_price_minor=500)],
        payment_method=METHOD_CASH,
        cash_received_minor=1000,
        actor=admin,
    )
    win._on_order_completed(order)
    assert receipt_exec_called == [order.order_number]

    # Verify 58mm receipt formatting renders narrower cleanly without crashing
    plain_58 = build_receipt_plain_text(order, cfg, paper_width_mm=58)
    doc_58 = build_receipt_document(order, cfg, paper_width_mm=58)
    assert order.order_number in plain_58
    assert order.order_number in doc_58.toPlainText()

    # Backup ZIP roundtrip preserves Schema v8 tables (shifts, ingredients, recipes, expenses)
    bkp = get_backups_dir() / "schema_v8_full.zip"
    create_backup_archive(db, bkp, actor=admin)
    info = inspect_and_validate_backup(bkp)
    assert info.schema_version == 8

    # Mutate live DB then restore
    db.delete_expense(exp.id, actor=admin)
    db.close_shift(shift.id, counted_cash_minor=11000, actor=admin)
    assert db.list_expenses() == []
    assert db.get_active_shift(admin.id, actor=admin) is None

    restore_backup_archive(db, bkp, actor=admin)
    assert len(db.list_expenses()) == 1
    assert db.list_expenses()[0].title == "Water Bill"
    assert db.get_active_shift(admin.id, actor=admin) is not None
    assert db.get_product_recipe(latte.id)[0].ingredient_id == milk.id
    win.close()


def test_bugfixes_refund_cogs_partial_cost_category_proration_mix_and_expenses(
    isolated_data_dir: Path, qapp
) -> None:
    db, admin, _cashier = _seed_users_and_store(isolated_data_dir)
    today_d = date.today()

    # Bug #6: 2-ingredient recipe where 1 ingredient lacks unit cost -> NOT reliably costed
    tea_leaf = db.create_ingredient(
        "Matcha Leaf", unit="g", stock_qty_milli=500_000, cost_per_unit_minor=20, actor=admin
    )
    tap_water = db.create_ingredient(
        "Filtered Water", unit="ml", stock_qty_milli=5_000_000, cost_per_unit_minor=0, actor=admin
    )
    incomplete_tea = db.add_product("Water Matcha", "Tea", 800, actor=admin)
    db.set_product_recipe(incomplete_tea.id, [(tea_leaf.id, 10_000), (tap_water.id, 200_000)], actor=admin)
    ord_inc = db.create_order_with_payment(
        [CartLine(product_id=incomplete_tea.id, name=incomplete_tea.name, unit_price_minor=800, quantity=1, base_unit_price_minor=800)],
        payment_method=METHOD_CASH,
        cash_received_minor=800,
        actor=admin,
    )
    with db._tx() as conn:
        r_inc = conn.execute("SELECT has_cost_data FROM orders WHERE id = ?;", (ord_inc.id,)).fetchone()
        assert int(r_inc["has_cost_data"]) == 0

    # Reset cleanly via second isolated scenario: use a fresh category "Specialty Tea" + "Pastry"
    paid_water = db.create_ingredient(
        "Spring Water", unit="ml", stock_qty_milli=5_000_000, cost_per_unit_minor=1, actor=admin
    )
    costed_tea = db.add_product("Ceremonial Bowl", "Specialty Tea", 1000, actor=admin)
    # 10g * 20 + 100ml * 1 = 200 + 100 = 300 minor COGS
    db.set_product_recipe(costed_tea.id, [(tea_leaf.id, 10_000), (paid_water.id, 100_000)], actor=admin)
    uncosted_pastry = db.add_product("Plain Cookie", "Pastry", 500, actor=admin)

    # Mixed multi-category order paid via CASH ($10 Tea + $5 Cookie = $15 total)
    mixed_ord = db.create_order_with_payment(
        [
            CartLine(product_id=costed_tea.id, name=costed_tea.name, unit_price_minor=1000, quantity=1, base_unit_price_minor=1000),
            CartLine(product_id=uncosted_pastry.id, name=uncosted_pastry.name, unit_price_minor=500, quantity=1, base_unit_price_minor=500),
        ],
        payment_method=METHOD_CASH,
        cash_received_minor=1500,
        actor=admin,
    )
    # Bug #6: mixed order has uncosted item -> whole order has_cost_data == 0
    with db._tx() as conn:
        r_mix = conn.execute("SELECT has_cost_data FROM orders WHERE id = ?;", (mixed_ord.id,)).fetchone()
        assert int(r_mix["has_cost_data"]) == 0

    # Bug #2: Category filter on "Specialty Tea" prorates revenue ($10.00) & isolates costed Tea COGS ($3.00 -> $7.00 profit)
    ext_tea = db.get_extended_analytics(
        start_date=today_d,
        end_date=today_d,
        category="Specialty Tea",
        actor=admin,
    )
    assert ext_tea.net_revenue_minor == 1000
    assert ext_tea.cash_sales_minor == 1000
    assert ext_tea.has_reliable_cost_data is True
    assert ext_tea.estimated_cogs_minor == 300
    assert ext_tea.estimated_gross_profit_minor == 700
    assert [o.id for o in ext_tea.recent_orders] == [mixed_ord.id]

    # Filtering by "Pastry" must remain cost-gated (has_reliable_cost_data == False)
    ext_pastry = db.get_extended_analytics(
        start_date=today_d,
        end_date=today_d,
        category="Pastry",
        actor=admin,
    )
    assert ext_pastry.net_revenue_minor == 500
    assert ext_pastry.has_reliable_cost_data is False

    # Pure costed Card order ($10.00, $3.00 COGS) -> full refund RETURNED reverses COGS (Bug #1) & deducts Card mix (Bug #3)
    card_ord = db.create_order_with_payment(
        [CartLine(product_id=costed_tea.id, name=costed_tea.name, unit_price_minor=1000, quantity=1, base_unit_price_minor=1000)],
        payment_method=METHOD_CARD,
        confirmed_external=True,
        actor=admin,
    )
    card_item_id = card_ord.items[0].id
    db.create_order_refund(
        card_ord.id,
        [{"order_item_id": card_item_id, "quantity": 1, "stock_action": "RETURNED"}],
        refund_method=METHOD_CARD,
        reason="Customer changed mind",
        actor=admin,
    )
    ext_card = db.get_extended_analytics(
        start_date=today_d,
        end_date=today_d,
        payment_method=METHOD_CARD,
        actor=admin,
    )
    assert ext_card.net_revenue_minor == 0
    assert ext_card.refunds_total_minor == 1000
    assert ext_card.digital_sales_minor == 0
    assert ext_card.payment_breakdown[0].amount_minor == 0
    assert ext_card.estimated_cogs_minor == 0
    assert ext_card.estimated_gross_profit_minor == 0

    # Bug #4: Early morning (02:00 local) expense creates visible 02:00 bucket in hour mode
    early_local = datetime.combine(today_d, datetime.min.time()).replace(hour=2, minute=15).astimezone()
    early_utc = early_local.astimezone(timezone.utc).isoformat()
    exp = db.create_expense("SUPPLIES", "Early Ice Delivery", 2500, today_d.isoformat(), actor=admin)
    with db._tx() as conn:
        conn.execute("UPDATE expenses SET created_at = ? WHERE id = ?;", (early_utc, exp.id))
    ext_hr = db.get_extended_analytics(start_date=today_d, end_date=today_d, bucket_mode="hour", actor=admin)
    b02 = [b for b in ext_hr.buckets if b.key == "02:00"]
    assert len(b02) == 1
    assert b02[0].expenses_minor == 2500

    # Bug #5: ReportsView.recent_table respects filtered ext.recent_orders
    win = MainWindow(db, qapp)
    win.login_user(admin)
    win.switch_view(3)
    rep = win.reports_view
    rep._set_period("today")
    rep.cat_chart_widget.category_clicked.emit("Specialty Tea")
    qapp.processEvents()
    # Both mixed_ord and card_ord contain Specialty Tea (unlike ord_inc which is in "Tea")
    shown_nums = {rep.recent_table.item(r, 0).text() for r in range(rep.recent_table.rowCount())}
    assert mixed_ord.order_number in shown_nums
    assert card_ord.order_number in shown_nums
    assert ord_inc.order_number not in shown_nums
    win.close()


def test_bugfixes_unsaved_modifier_option_recipe_and_qt_ar_translator(
    isolated_data_dir: Path, qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, admin, _cashier = _seed_users_and_store(isolated_data_dir)
    cfg = db.get_settings()
    db.set_session_actor(admin)

    oat = db.create_ingredient("Oat Milk", unit="ml", stock_qty_milli=1_000_000, cost_per_unit_minor=2, actor=admin)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: QMessageBox.Ok)

    # Bug #8: Arabic Qt standard button translator installs/uninstalls live
    set_language("ar-EG")
    assert QCoreApplication.translate("QPlatformTheme", "&Yes") == "نعم"
    assert QCoreApplication.translate("QDialogButtonBox", "Cancel") == "إلغاء"
    set_language("en")
    assert QCoreApplication.translate("QPlatformTheme", "&Yes") == "&Yes"

    # Bug #7: New unsaved modifier option row can configure Recipe BEFORE saving group (and reopen tuple list!)
    dlg = ModifierManagerDialog(db, cfg)
    dlg._start_new_group()
    dlg.name_en_edit.setText("Milk Upgrades")
    en_w = dlg.options_table.cellWidget(0, 0)
    en_w.setText("Oat Splash")

    # Stage recipe twice via deferred OptionRecipeDialog (first empty -> [(oat.id, 80_000)], then reopen tuples -> [(oat.id, 90_000)])
    act_cell = dlg.options_table.cellWidget(0, 4)
    rec_btn = [pb for pb in act_cell.findChildren(QPushButton) if isinstance(pb.property("pending_recipe"), list)][0]
    assert rec_btn.isEnabled() is True

    calls = [0]

    def fake_opt_exec(self_opt: OptionRecipeDialog) -> int:
        calls[0] += 1
        if calls[0] == 1:
            self_opt.editor.load_entry_pairs([(oat.id, 80_000)])
        else:
            # Ensure reopening loaded the 80_000 staged pair cleanly without crashing!
            assert self_opt.editor.entries() == [(oat.id, 80_000)]
            self_opt.editor.load_entry_pairs([(oat.id, 90_000)])
        self_opt.result_entries = self_opt.editor.entries()
        return OptionRecipeDialog.Accepted

    monkeypatch.setattr(OptionRecipeDialog, "exec", fake_opt_exec)
    rec_btn.click()
    assert "(1)" in rec_btn.text()
    rec_btn.click()
    assert rec_btn.property("pending_recipe") == [(oat.id, 90_000)]

    dlg._save_current_group()
    groups = db.list_modifier_groups()
    assert len(groups) == 1
    saved_opt = groups[0].options[0]
    saved_rec = db.get_modifier_option_recipe(saved_opt.id)
    assert len(saved_rec) == 1
    assert saved_rec[0].ingredient_id == oat.id
    assert saved_rec[0].qty_milli == 90_000
    dlg.close()
