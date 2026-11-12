import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QLineEdit, QPushButton

from cafe_pos.auth import AuthorizationError, ROLE_CASHIER
from cafe_pos.backups import (
    create_backup_archive,
    inspect_and_validate_backup,
    restore_backup_archive,
)
from cafe_pos.bilingual import suggest_bilingual_name, suggest_bilingual_pair
from cafe_pos.config import BusinessConfig
from cafe_pos.db import CURRENT_SCHEMA_VERSION, CartLine, Database
from cafe_pos.icons import get_bundled_product_asset_path
from cafe_pos.i18n import set_language, tr
from cafe_pos.image_search import suggest_image_queries
from cafe_pos.paths import (
    get_backups_dir,
    get_database_path,
    get_default_receipts_dir,
    get_product_images_dir,
)
from cafe_pos.payments import (
    COUNTRY_PRESETS,
    CustomPaymentMethodDef,
    METHOD_APPLE_PAY,
    METHOD_CARD,
    METHOD_CASH,
    METHOD_CUSTOM,
    METHOD_EWALLET,
    METHOD_INSTAPAY,
    PaymentSettingsConfig,
    STATUS_CASH_SETTLED,
    STATUS_CONFIRMED_EXTERNAL,
    apply_country_preset,
    default_payment_settings,
    serialize_payment_settings,
    validate_external_reference,
)
from cafe_pos.receipts import build_receipt_plain_text
from cafe_pos.ui.main_window import MainWindow
from cafe_pos.ui.payment_dialog import CheckoutPaymentDialog
from cafe_pos.ui.product_editor_dialog import ProductEditorDialog
from cafe_pos.ui.setup_dialog import SetupDialog


def _setup_admin_and_cashier(db: Database, *, country: str = "US", currency: str = "USD"):
    pm_cfg = default_payment_settings(country)
    cfg, admin = db.initialize_owner_and_business(
        business_cfg=BusinessConfig(
            business_name="Nile Boutique Café",
            currency=currency,
            business_country=country,
            payment_methods_json=serialize_payment_settings(pm_cfg),
            setup_completed=True,
        ),
        admin_username="owner",
        admin_password="OwnerStrong!2026",
        admin_display_name="Owner Admin",
    )
    db.set_session_actor(admin)
    cashier = db.create_user(
        "sara",
        "CashierStrong!2026",
        ROLE_CASHIER,
        display_name="Sara M",
        actor=admin,
    )
    return cfg, admin, cashier


# =====================================================================
# 1. SMART CATEGORIES, RBAC, INLINE UI & SNAPSHOT PRESERVATION
# =====================================================================


def test_smart_categories_crud_duplicates_move_archive_rbac_and_history_snapshot(
    qapp, isolated_data_dir: Path
):
    db = Database(get_database_path())
    cfg, admin, cashier = _setup_admin_and_cashier(db)

    # Admin creates bilingual category cleanly (EN -> AR offline suggestion + AR -> EN)
    cat_mojito = db.create_category("موهيتو", actor=admin)
    assert cat_mojito.name_ar == "موهيتو"
    assert cat_mojito.name_en == "Mojito"

    cat_waffles = db.create_category("Waffles", name_ar="وافل", actor=admin)
    assert cat_waffles.name_en == "Waffles"
    assert cat_waffles.name_ar == "وافل"

    # Duplicate detection: exact, case-insensitive, tatweel, diacritics, and EN<->AR synonym
    for dup_name in ("waffles", "اسـبريـسو", "مَخْبُوزَات", "إسبريسو"):
        with pytest.raises(ValueError):
            db.create_category(dup_name, actor=admin)

    # Reserved "All" / "كل القائمة" rejected
    for reserved in ("All", "كل القائمة"):
        with pytest.raises(ValueError):
            db.create_category(reserved, actor=admin)

    # Cashier blocked at DB layer for all category mutations
    with pytest.raises(AuthorizationError):
        db.create_category("CashierCat", actor=cashier)
    with pytest.raises(AuthorizationError):
        db.rename_category(cat_waffles.id, "BlockedRename", actor=cashier)
    with pytest.raises(AuthorizationError):
        db.delete_category(cat_waffles.id, actor=cashier)
    with pytest.raises(AuthorizationError):
        db.archive_category(cat_waffles.id, actor=cashier)
    with pytest.raises(AuthorizationError):
        db.restore_category(cat_waffles.id, actor=cashier)

    # Add product + complete historical order before category rename
    belgian_waffle = db.add_product(
        "Belgian Waffle",
        cat_waffles.primary_name,
        750,
        category_id=cat_waffles.id,
        name_en="Belgian Waffle",
        name_ar="وافل بلجيكي",
        actor=admin,
    )
    order1 = db.create_order(
        [
            CartLine(
                product_id=belgian_waffle.id,
                name=belgian_waffle.name,
                unit_price_minor=750,
                quantity=1,
            )
        ],
        1000,
        actor=admin,
    )

    # Rename category EN & AR -> updates product's live category, preserves historical order snapshot
    renamed = db.rename_category(
        cat_waffles.id,
        "Gourmet Waffles",
        name_en="Gourmet Waffles",
        name_ar="وافل فاخر",
        actor=admin,
    )
    assert renamed.name_en == "Gourmet Waffles"
    assert renamed.name_ar == "وافل فاخر"
    refreshed_prod = db.list_products(search="Belgian Waffle")[0]
    assert refreshed_prod.category == "Gourmet Waffles"

    analytics = db.get_sales_analytics("today", actor=admin)
    cats_sales = {c.category: c for c in analytics.category_sales}
    assert "Waffles" in cats_sales
    assert "Gourmet Waffles" not in cats_sales
    assert cats_sales["Waffles"].total_minor == order1.total_minor

    # Empty category deletes cleanly
    assert db.delete_category(cat_mojito.id, actor=admin) == "deleted"

    # Non-empty category cannot be hard-deleted without move or archive
    with pytest.raises(ValueError):
        db.delete_category(cat_waffles.id, actor=admin)

    # Archive hides from Register/active list, then Restore brings it back
    db.archive_category(cat_waffles.id, actor=admin)
    assert "Gourmet Waffles" not in db.list_categories(include_archived=False)
    assert "Gourmet Waffles" in db.list_categories(include_archived=True)
    db.restore_category(cat_waffles.id, actor=admin)
    assert "Gourmet Waffles" in db.list_categories(include_archived=False)

    # Move products & delete deletes old category and reassigns product cleanly
    bakery_cat = next(c for c in db.list_category_records() if c.name_en == "Bakery")
    assert db.delete_category(cat_waffles.id, move_to_category_id=bakery_cat.id, actor=admin) == "moved_and_deleted"
    reassigned = db.list_products(search="Belgian Waffle")[0]
    assert reassigned.category == "Bakery"
    assert reassigned.category_id == bakery_cat.id


def test_smart_category_combobox_pending_cancel_commit_cashier_readonly_and_no_standalone_tab(
    qapp, isolated_data_dir: Path
):
    db = Database(get_database_path())
    cfg, admin, _cashier = _setup_admin_and_cashier(db)

    initial_cat_count = len(db.list_category_records(include_archived=True))

    # 1. Admin stages pending category in ProductEditorDialog and clicks Cancel -> NOT saved to DB!
    dlg = ProductEditorDialog(cfg, db.list_categories(), product=None, db=db, is_admin=True)
    dlg.category_combo._on_line_text_edited("Matcha Specials")
    assert dlg.category_combo._popup.isVisible() is True
    payloads = [
        dlg.category_combo._list.item(i).data(Qt.UserRole)
        for i in range(dlg.category_combo._list.count())
    ]
    assert any(p and p[0] == "create" and p[1] == "Matcha Specials" for p in payloads)
    dlg.category_combo.stage_pending_category("Matcha Specials")
    sel = dlg.category_combo.resolve_selection()
    assert sel.is_pending_new is True
    assert dlg.pending_cat_badge.isHidden() is False
    dlg.reject()
    assert len(db.list_category_records(include_archived=True)) == initial_cat_count

    # 2. Cashier mode hides "+ Create" & "⋯" actions and rejects saving a new category name
    dlg_cashier = ProductEditorDialog(cfg, db.list_categories(), product=None, db=db, is_admin=False)
    dlg_cashier.category_combo.showPopup()
    for i in range(dlg_cashier.category_combo._list.count()):
        item = dlg_cashier.category_combo._list.item(i)
        w = dlg_cashier.category_combo._list.itemWidget(item)
        if w is not None and hasattr(w, "more_btn"):
            assert w.more_btn is None
    dlg_cashier.category_combo._on_line_text_edited("Unauthorized Category")
    payloads_c = [
        dlg_cashier.category_combo._list.item(i).data(Qt.UserRole)
        for i in range(dlg_cashier.category_combo._list.count())
    ]
    assert not any(p and p[0] == "create" for p in payloads_c)
    assert dlg_cashier._validate_form_live() is False
    dlg_cashier.reject()

    # 3. Saving through MainWindow -> ProductsView with staged pending category commits category + product
    window = MainWindow(db, qapp)
    try:
        window.login_user(admin)
        # Verify NO standalone "Categories" nav button or standalone category management button
        nav_texts = [b.text().lower() for b in window.nav_buttons]
        assert not any("categor" in t for t in nav_texts)
        pv = window.products_view
        pv_btn_texts = [b.text().lower() for b in pv.findChildren(QPushButton)]
        assert not any("categor" in t for t in pv_btn_texts)

        def _fill_and_save_editor(attempt: int = 0) -> None:
            m = qapp.activeModalWidget()
            if isinstance(m, ProductEditorDialog):
                m.name_edit.setText("Fresh Mango Juice")
                m.category_combo.stage_pending_category("Fresh Juices")
                m.price_edit.setText("6.25")
                m.save_btn.click()
            elif attempt < 20:
                QTimer.singleShot(10, lambda: _fill_and_save_editor(attempt + 1))

        QTimer.singleShot(0, _fill_and_save_editor)
        pv.open_add_dialog()

        cats_after = db.list_category_records(include_archived=True)
        fj = next((c for c in cats_after if c.name_en == "Fresh Juices"), None)
        assert fj is not None
        assert fj.name_ar == "عصائر طازجة"
        saved_prod = db.list_products(search="Fresh Mango Juice")[0]
        assert saved_prod.category_id == fj.id
    finally:
        window.close()


# =====================================================================
# 2. BILINGUAL PRODUCTS & OFFLINE-FIRST IMAGE/CUTOUT RESOLUTION
# =====================================================================


def test_bilingual_product_autofill_cutout_queries_and_offline_default(
    qapp, isolated_data_dir: Path
):
    db = Database(get_database_path())
    cfg, admin, _cashier = _setup_admin_and_cashier(db)

    # Online translation strictly disabled by default
    assert cfg.online_translation_enabled is False

    # EN product auto-fills Arabic suggestion; manual alt edit is respected
    dlg = ProductEditorDialog(cfg, db.list_categories(), product=None, db=db, is_admin=True)
    assert dlg.online_translate_btn.isHidden() is True
    dlg.name_edit.setText("Iced Latte")
    assert dlg.alt_name_edit.text() == "لاتيه مثلج"

    # Simulate user typing a custom Arabic translation -> subsequent typing does not overwrite
    dlg.alt_name_edit.setText("لاتيه بارد خاص")
    dlg._on_alt_name_edited("لاتيه بارد خاص")
    dlg.name_edit.setText("Iced Caramel Latte")
    assert dlg.alt_name_edit.text() == "لاتيه بارد خاص"
    dlg.reject()

    # Arabic primary product ("فلات وايت") -> auto-suggests EN ("Flat White") & finds cutout asset
    dlg_ar = ProductEditorDialog(cfg, db.list_categories(), product=None, db=db, is_admin=True)
    dlg_ar.name_edit.setText("فلات وايت")
    assert dlg_ar.alt_name_edit.text() == "Flat White"
    assert dlg_ar.photo_search_input.text().strip() == "Flat White"
    assert "Built-in studio" in dlg_ar.image_status_lbl.text() or dlg_ar.preview_lbl.pixmap() is not None
    assert get_bundled_product_asset_path("فلات وايت") == get_bundled_product_asset_path("Flat White")

    queries = suggest_image_queries("لاتيه بالفانيليا", "Espresso", product_name_en="Vanilla Latte")
    assert len(queries) >= 1 and "Vanilla Latte" in queries[0]
    dlg_ar.reject()

    # Toggle online_translation_enabled = True -> button becomes visible in editor
    cfg.online_translation_enabled = True
    saved_cfg = db.save_settings(cfg, actor=admin)
    assert saved_cfg.online_translation_enabled is True
    dlg_on = ProductEditorDialog(saved_cfg, db.list_categories(), product=None, db=db, is_admin=True)
    assert dlg_on.online_translate_btn.isHidden() is False
    dlg_on.reject()


# =====================================================================
# 3. REGIONAL PAYMENTS, PRESETS, CUSTOMIZATION PROTECTION & CURRENCY
# =====================================================================


def test_regional_payment_presets_settings_protection_currency_and_setup(
    qapp, isolated_data_dir: Path
):
    db = Database(get_database_path())
    cfg, admin, _cashier = _setup_admin_and_cashier(db, country="EG", currency="EGP")

    # Verify regional defaults
    eg = default_payment_settings("EG")
    assert eg.enabled_methods == [METHOD_CASH, METHOD_APPLE_PAY, METHOD_INSTAPAY, METHOD_EWALLET]
    assert METHOD_CARD not in eg.enabled_methods
    assert "Meeza" in eg.card_brands
    assert "Vodafone Cash" in eg.wallet_providers
    assert "e& money" in eg.wallet_providers

    sa = default_payment_settings("SA")
    assert sa.enabled_methods == [METHOD_CASH, METHOD_CARD, METHOD_APPLE_PAY, METHOD_EWALLET]
    assert "mada" in sa.card_brands

    ae = default_payment_settings("AE")
    assert ae.enabled_methods == [METHOD_CASH, METHOD_CARD, METHOD_APPLE_PAY, METHOD_EWALLET]
    assert "Google Pay" in ae.wallet_providers

    # Customized flag prevents country change from silently overwriting admin choices
    custom_pm = PaymentSettingsConfig(
        country="EG",
        customized=True,
        enabled_methods=[METHOD_CASH, METHOD_INSTAPAY],
        card_brands=["Meeza"],
        wallet_providers=["Vodafone Cash"],
        custom_methods=[CustomPaymentMethodDef(id="valu", label_en="ValU", label_ar="فاليو")],
    )
    cfg.payment_methods_json = serialize_payment_settings(custom_pm)
    saved = db.save_settings(cfg, actor=admin)

    window = MainWindow(db, qapp)
    try:
        window.login_user(admin)
        window.switch_view(5)
        sv = window.settings_view

        # Record product minor price before changing currency
        espresso = next(p for p in db.list_products() if p.name == "Double Espresso")
        orig_minor = espresso.price_minor

        # Switching country while customized preserves custom methods & enabled toggles
        sa_idx = sv.country_combo.findData("SA")
        sv.country_combo.setCurrentIndex(sa_idx)
        assert sv.chk_pay_instapay.isChecked() is True
        assert sv.chk_pay_card.isChecked() is False
        assert len(sv._draft_custom_methods) == 1

        # Clicking explicit "Apply Regional Preset" resets standard methods/brands to SA + currency SAR
        sv.apply_preset_btn.click()
        assert sv.chk_pay_card.isChecked() is True
        assert sv.chk_pay_instapay.isChecked() is False
        assert "mada" in sv.card_brands_edit.text()
        assert sv.currency_combo.currentData() == "SAR"

        sv._save_settings()
        assert db.get_settings().currency == "SAR"
        # Changing currency NEVER mutates existing products.price_minor
        espresso_after = next(p for p in db.list_products() if p.id == espresso.id)
        assert espresso_after.price_minor == orig_minor
    finally:
        window.close()

    # SetupDialog persists country_combo & regional payment preset cleanly
    setup_dlg = SetupDialog(
        BusinessConfig(business_name="Cairo Brew", currency="USD", business_country="US"),
        is_first_launch=False,
        require_admin_setup=False,
    )
    eg_idx = setup_dlg.country_combo.findData("EG")
    setup_dlg.country_combo.setCurrentIndex(eg_idx)
    built = setup_dlg._build_config("Cairo Brew")
    assert built.business_country == "EG"
    assert "INSTAPAY" in built.payment_methods_json
    setup_dlg.reject()


# =====================================================================
# 4. CHECKOUT PAYMENT DIALOG, NO-PAN SECURITY, IDEMPOTENCY & RECEIPTS
# =====================================================================


def test_checkout_payment_methods_security_pan_rejection_and_idempotency(
    qapp, isolated_data_dir: Path
):
    db = Database(get_database_path())
    cfg, admin, cashier = _setup_admin_and_cashier(db, country="EG", currency="EGP")

    # Configure ValU custom method + require_instapay_reference = True + optional CARD terminal
    pm_cfg = default_payment_settings("EG")
    pm_cfg.enabled_methods = [METHOD_CASH, METHOD_APPLE_PAY, METHOD_INSTAPAY, METHOD_EWALLET, METHOD_CARD]
    pm_cfg.require_instapay_reference = True
    pm_cfg.custom_methods = [
        CustomPaymentMethodDef(
            id="valu",
            label_en="ValU",
            label_ar="فاليو",
            requires_reference=True,
            enabled=True,
        )
    ]
    cfg.payment_methods_json = serialize_payment_settings(pm_cfg)
    cfg = db.save_settings(cfg, actor=admin)

    prod = db.list_products()[0]
    lines = [
        CartLine(
            product_id=prod.id,
            name=prod.name,
            unit_price_minor=10000,  # 100.00 EGP
            quantity=1,
            category=prod.category,
            name_en=prod.name_en,
            name_ar=prod.name_ar,
        )
    ]

    # Security negative check: CheckoutPaymentDialog contains ZERO card PAN/CVV/PIN/expiry inputs
    sec_dlg = CheckoutPaymentDialog(db, cfg, lines)
    for le in sec_dlg.findChildren(QLineEdit):
        assert le.echoMode() != QLineEdit.Password
        ph = (le.placeholderText() + " " + le.objectName()).lower()
        assert not any(tok in ph for tok in ("cvv", "cvc", "pin", "expiry", "expiration"))

    # DB schema CHECK constraint rejects fabricated 'provider_verified' payment status
    with sqlite3.connect(str(get_database_path())) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO payments (
                    order_id, payment_method, payment_amount_minor, currency,
                    payment_status, confirmed_at, verification_source
                ) VALUES (1, 'CARD', 1000, 'EGP', 'provider_verified', '2026-01-01T00:00:00Z', 'gateway');
                """
            )

    # PAN rejection (13-19 digits, plain or spaced/dashed)
    for bad_pan in ("4111111111111111", "4111 1111 1111 1111", "5200-0000-0000-1234", "3400123456789"):
        with pytest.raises(ValueError):
            validate_external_reference(bad_pan, required=False)
    assert validate_external_reference("AUTH-9842 last4 1234") == "AUTH-9842 last4 1234"

    # Cash checkout via Arabic-Indic digits (١٥٠٫٠٠ -> 150.00 EGP, change 50.00 EGP)
    db.set_session_actor(cashier)
    sec_dlg.select_method(METHOD_CASH)
    sec_dlg.cash_edit.setText("١٥٠٫٠٠")
    assert sec_dlg.confirm_btn.isEnabled() is True
    sec_dlg._on_confirm()
    assert sec_dlg.result() == QDialog.Accepted
    cash_order = sec_dlg.result_order
    assert cash_order is not None
    assert cash_order.receipt_number.startswith("RCP-")
    assert cash_order.primary_payment is not None
    assert cash_order.primary_payment.payment_method == METHOD_CASH
    assert cash_order.primary_payment.payment_status == STATUS_CASH_SETTLED
    assert cash_order.change_minor == 5000

    # Card checkout: PAN in ref disables confirm; valid ref + checkbox confirms
    card_dlg = CheckoutPaymentDialog(db, cfg, lines)
    assert card_dlg.select_method(METHOD_CARD) is True
    card_dlg.card_brand_combo.setCurrentText("Meeza")
    card_dlg.card_confirm_chk.setChecked(True)
    card_dlg.card_ref_edit.setText("4111 1111 1111 1111")
    assert card_dlg.confirm_btn.isEnabled() is False
    assert card_dlg.validation_lbl.isHidden() is False
    card_dlg.card_ref_edit.setText("POS-4421")
    assert card_dlg.confirm_btn.isEnabled() is True
    card_dlg._on_confirm()
    card_order = card_dlg.result_order
    assert card_order is not None
    assert card_order.primary_payment.payment_method == METHOD_CARD
    assert card_order.primary_payment.payment_provider == "Meeza"
    assert card_order.primary_payment.payment_status == STATUS_CONFIRMED_EXTERNAL
    assert card_order.primary_payment.external_reference == "POS-4421"

    # InstaPay with required ref
    ip_dlg = CheckoutPaymentDialog(db, cfg, lines)
    ip_dlg.select_method(METHOD_INSTAPAY)
    ip_dlg.instapay_confirm_chk.setChecked(True)
    assert ip_dlg.confirm_btn.isEnabled() is False  # reference required!
    ip_dlg.instapay_ref_edit.setText("IPN-771")
    assert ip_dlg.confirm_btn.isEnabled() is True
    ip_dlg._on_confirm()
    assert ip_dlg.result_order.primary_payment.payment_method == METHOD_INSTAPAY

    # Custom ValU method + E-Wallet method
    cust_dlg = CheckoutPaymentDialog(db, cfg, lines)
    cust_dlg.select_method("CUSTOM:valu")
    cust_dlg.custom_ref_edit.setText("VALU-555")
    cust_dlg.custom_confirm_chk.setChecked(True)
    cust_dlg._on_confirm()
    cust_order = cust_dlg.result_order
    assert cust_order.primary_payment.payment_method == METHOD_CUSTOM
    assert cust_order.primary_payment.custom_method_label == "ValU"

    ew_dlg = CheckoutPaymentDialog(db, cfg, lines)
    ew_dlg.select_method(METHOD_EWALLET)
    ew_dlg.ewallet_provider_combo.setCurrentText("Vodafone Cash")
    ew_dlg.ewallet_confirm_chk.setChecked(True)
    ew_dlg._on_confirm()
    assert ew_dlg.result_order.primary_payment.payment_provider == "Vodafone Cash"

    # Client idempotency key prevents duplicate order creation
    seq_before = db.get_order_count()
    idem1 = db.create_order_with_payment(
        lines,
        payment_method=METHOD_CARD,
        payment_provider="Visa",
        confirmed_external=True,
        client_tx_key="idem-test-001",
        actor=cashier,
    )
    idem2 = db.create_order_with_payment(
        lines,
        payment_method=METHOD_CARD,
        payment_provider="Visa",
        confirmed_external=True,
        client_tx_key="idem-test-001",
        actor=cashier,
    )
    assert idem1.id == idem2.id
    assert db.get_order_count() == seq_before + 1


# =====================================================================
# 5. SALES VIEW RECEIPTS, AUTO-PDF EXPORT, BACKUP/RESTORE & V4 MIGRATION
# =====================================================================


def test_sales_view_filters_auto_pdf_resilience_and_v5_backup_with_receipts(
    qapp, isolated_data_dir: Path, tmp_path: Path, monkeypatch
):
    db = Database(get_database_path())
    cfg, admin, _cashier = _setup_admin_and_cashier(db, country="EG", currency="EGP")

    export_dir = tmp_path / "exported_receipts"
    cfg.auto_save_receipt_pdf = True
    cfg.receipt_export_dir = str(export_dir)
    cfg = db.save_settings(cfg, actor=admin)

    window = MainWindow(db, qapp)
    try:
        window.login_user(admin)
        prod = db.list_products()[0]

        captured_dialogs: list[object] = []

        def _fake_exec(self_dlg):
            captured_dialogs.append(self_dlg)
            return 1

        monkeypatch.setattr("cafe_pos.ui.main_window.ReceiptDialog.exec", _fake_exec)

        # Complete Apple Pay checkout via RegisterView -> CheckoutPaymentDialog
        window.register_view.add_product_to_cart(prod)

        def _drive_apple_pay_checkout(attempt: int = 0) -> None:
            m = qapp.activeModalWidget()
            if isinstance(m, CheckoutPaymentDialog):
                assert m.select_method(METHOD_APPLE_PAY) is True
                m.apple_pay_ref_edit.setText("AUTH-314")
                m.apple_pay_confirm_chk.setChecked(True)
                if m.confirm_btn.isEnabled():
                    m.confirm_btn.click()
                else:
                    m.reject()
            elif attempt < 30:
                QTimer.singleShot(10, lambda: _drive_apple_pay_checkout(attempt + 1))

        QTimer.singleShot(0, _drive_apple_pay_checkout)
        window.register_view.trigger_checkout()

        # Verify A5 PDF auto-exported & badge shown inside ReceiptDialog
        pdfs = list(export_dir.glob("Receipt-RCP-*.pdf"))
        assert len(pdfs) == 1
        assert pdfs[0].read_bytes().startswith(b"%PDF-")
        assert len(captured_dialogs) == 1
        assert captured_dialogs[0].auto_saved_badge.isHidden() is False

        # OSError during PDF export must NOT crash or fail completed checkout!
        def _boom(*_args, **_kwargs):
            raise OSError("Simulated disk full")

        monkeypatch.setattr("cafe_pos.ui.main_window.export_receipt_pdf", _boom)
        window.register_view.add_product_to_cart(prod)

        def _drive_cash_checkout(attempt: int = 0) -> None:
            m = qapp.activeModalWidget()
            if isinstance(m, CheckoutPaymentDialog):
                m.select_method(METHOD_CASH)
                m.cash_edit.setText("100.00")
                if m.confirm_btn.isEnabled():
                    m.confirm_btn.click()
                else:
                    m.reject()
            elif attempt < 30:
                QTimer.singleShot(10, lambda: _drive_cash_checkout(attempt + 1))

        QTimer.singleShot(0, _drive_cash_checkout)
        window.register_view.trigger_checkout()
        assert db.get_order_count() == 2
        assert len(captured_dialogs) == 2
        assert captured_dialogs[1].auto_saved_badge.isHidden() is True

        # SalesView 6 columns + payment filter + RCP/reference search + meta badge
        window.switch_view(2)
        sv = window.sales_view
        assert sv.table.columnCount() == 6
        assert sv.table.rowCount() == 2

        ap_idx = sv.payment_filter.findData(METHOD_APPLE_PAY)
        sv.payment_filter.setCurrentIndex(ap_idx)
        assert sv.table.rowCount() == 1
        assert "AUTH-314" in sv.receipt_preview.toPlainText()
        assert "RCP-1001" in sv.meta_left_lbl.text()

        sv.payment_filter.setCurrentIndex(0)
        sv.search_input.setText("AUTH-314")
        assert sv.table.rowCount() == 1
        sv.search_input.clear()

        # v6 ZIP backup bundles SQLite, receipts/*.pdf, and product_images/ with SHA-256 verification
        zip_path = create_backup_archive(
            db, get_backups_dir() / "CafePOS_v5_Test.zip", backup_kind="manual", actor=admin
        )
        info = inspect_and_validate_backup(zip_path)
        assert info.schema_version == CURRENT_SCHEMA_VERSION
        assert info.category_count >= 6
        assert info.payment_count == 2
        assert info.receipt_pdf_count >= 1

        # Tampering receipts/*.pdf triggers SHA-256 rejection
        tampered_rec_zip = tmp_path / "tampered_receipt.zip"
        with zipfile.ZipFile(zip_path, "r") as zin, zipfile.ZipFile(tampered_rec_zip, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.startswith("receipts/") and data:
                    data = data + b"TAMPER"
                zout.writestr(item, data)
        with pytest.raises(ValueError, match="SHA-256"):
            inspect_and_validate_backup(tampered_rec_zip)

        # Delete exported PDF from default dir & add extra order -> restore recovers DB & receipt PDF!
        managed_pdf = get_default_receipts_dir() / pdfs[0].name
        if managed_pdf.exists():
            managed_pdf.unlink()
        db.create_order(
            [CartLine(product_id=prod.id, name=prod.name, unit_price_minor=prod.price_minor, quantity=1)],
            1000,
            actor=admin,
        )
        assert db.get_order_count() == 3

        restore_info = restore_backup_archive(db, zip_path, actor=admin)
        assert restore_info.payment_count == 2
        assert db.get_order_count() == 2
        assert (get_default_receipts_dir() / pdfs[0].name).exists()
    finally:
        window.close()


def test_v4_to_v5_non_destructive_migration_backfills_categories_payments_and_receipts(
    isolated_data_dir: Path
):
    v4_path = get_database_path()
    with sqlite3.connect(str(v4_path)) as conn:
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
                next_order_seq INTEGER NOT NULL DEFAULT 1002,
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
                receipt_language TEXT NOT NULL DEFAULT 'follow'
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
            INSERT INTO app_meta VALUES ('seeded_defaults', '1'), ('schema_version', '4');
            INSERT INTO business_settings (id, business_name, accent_color, currency)
                VALUES (1, 'Legacy Roastery', '#005B37', 'EGP');
            INSERT INTO products (id, name, category, price_minor, created_at, updated_at)
                VALUES (1, 'Kunafa Tart', 'Signature Desserts', 6500, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
            INSERT INTO orders (
                id, order_number, created_at, currency, subtotal_minor, total_minor,
                cash_received_minor, change_minor, business_name, cashier_name_snapshot
            ) VALUES (
                1, 'ORD-1001', '2026-01-02T12:00:00Z', 'EGP', 6500, 6500, 7000, 500, 'Legacy Roastery', 'Legacy Cashier'
            );
            INSERT INTO order_items (id, order_id, product_id, product_name, unit_price_minor, quantity, line_total_minor)
                VALUES (1, 1, 1, 'Kunafa Tart', 6500, 1, 6500);
            """
        )

    db = Database(v4_path)
    with sqlite3.connect(str(v4_path)) as conn:
        ver = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version';").fetchone()[0]
    assert ver == str(CURRENT_SCHEMA_VERSION)

    # Pre-existing custom category linked into categories table + bilingual starter categories seeded
    cats = db.list_category_records(include_archived=True)
    sig = next((c for c in cats if c.name_en == "Signature Desserts"), None)
    assert sig is not None
    prod = db.list_products()[0]
    assert prod.category_id == sig.id

    # Legacy order backfilled with receipt_number = ORD-1001 and settled Cash payment record
    old_order = db.get_order(1)
    assert old_order is not None
    assert old_order.receipt_number == "ORD-1001"
    assert old_order.primary_payment is not None
    assert old_order.primary_payment.payment_method == METHOD_CASH
    assert old_order.primary_payment.payment_status == STATUS_CASH_SETTLED
    assert old_order.cashier_name_snapshot == "Legacy Cashier"

