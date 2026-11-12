import io
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog

from cafe_pos.config import BusinessConfig
from cafe_pos.db import CartLine, Database
from cafe_pos.icons import get_bundled_product_asset_path
from cafe_pos.image_search import ImageSearchResult
from cafe_pos.paths import get_asset_path, get_database_path, get_product_images_dir
from cafe_pos.receipts import build_receipt_document, build_receipt_plain_text, export_receipt_pdf
from cafe_pos.ui.main_window import MainWindow
from cafe_pos.ui.payment_dialog import CheckoutPaymentDialog
from cafe_pos.ui.product_editor_dialog import ProductEditorDialog
from cafe_pos.ui.setup_dialog import SetupDialog


def _schedule_confirm_modal_cash_checkout(qapp, cash_text: str = "20.00") -> None:
    def _confirm(attempt: int = 0) -> None:
        dlg = qapp.activeModalWidget()
        if isinstance(dlg, CheckoutPaymentDialog):
            dlg.cash_edit.setText(cash_text)
            if dlg.confirm_btn.isEnabled():
                dlg.confirm_btn.click()
            else:
                dlg.reject()
        elif attempt < 30:
            QTimer.singleShot(10, lambda: _confirm(attempt + 1))

    QTimer.singleShot(0, _confirm)


def _sample_jpeg_bytes(color=(180, 120, 80)) -> bytes:
    img = Image.new("RGB", (180, 180), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _setup_owner_and_sign_in(
    window: MainWindow,
    db: Database,
    *,
    business_name: str = "Green Grounds Café",
    currency: str = "USD",
    accent_color: str = "#005B37",
):
    cfg = BusinessConfig(
        business_name=business_name,
        accent_color=accent_color,
        currency=currency,
        address="100 Oak Ave",
        phone="555-0100",
        setup_completed=True,
    )
    saved_cfg, admin = db.initialize_owner_and_business(
        business_cfg=cfg,
        admin_username="owner",
        admin_password="OwnerStrongPassword!1",
        admin_display_name="Owner Admin",
    )
    window.config = saved_cfg
    window._apply_branding()
    window.login_user(admin)
    return saved_cfg, admin


def test_setup_dialog_two_step_customization_and_provider_preservation(qapp, isolated_data_dir: Path):
    db = Database(get_database_path())
    initial = BusinessConfig(
        business_name="Default",
        accent_color="#72917B",
        currency="USD",
        setup_completed=False,
        image_provider="pexels",
        pexels_api_key="my-secret-pexels-key",
    )
    dlg = SetupDialog(initial, is_first_launch=True, require_admin_setup=True)

    # Step 1 -> Step 2 navigation works cleanly
    assert dlg.step_stack.currentIndex() == 0
    dlg.name_edit.setText("Nile Brew Roasters")
    idx = dlg.currency_combo.findData("EGP")
    dlg.currency_combo.setCurrentIndex(idx)
    dlg._set_step(1)
    assert dlg.step_stack.currentIndex() == 1

    dlg._set_accent_color("#1E3A5F")
    dlg.address_edit.setText("55 Zamalek St")
    dlg.phone_edit.setText("+20 123 456 789")

    # Initial setup requires valid admin credentials (cannot bypass)
    dlg.admin_username_edit.setText("nileowner")
    dlg.admin_display_edit.setText("Tamer Owner")
    dlg.admin_password_edit.setText("short")
    dlg.admin_confirm_edit.setText("short")
    dlg._on_save()
    assert dlg.result_config is None
    assert not dlg.validation_lbl.isHidden()

    dlg.admin_password_edit.setText("BrewPass#2026")
    dlg.admin_confirm_edit.setText("BrewPass#2026")
    dlg._on_save()

    assert dlg.result_config is not None
    saved, admin_user = db.initialize_owner_and_business(
        business_cfg=dlg.result_config,
        admin_username=dlg.result_admin_username,
        admin_password=dlg.result_admin_password,
        admin_display_name=dlg.result_admin_display_name,
    )
    assert saved.business_name == "Nile Brew Roasters"
    assert saved.currency == "EGP"
    assert saved.accent_color == "#1E3A5F"
    assert saved.setup_completed is True
    # Preserved existing provider preferences
    assert saved.image_provider == "pexels"
    assert saved.pexels_api_key == "my-secret-pexels-key"
    assert admin_user.role == "ADMIN"
    assert admin_user.username == "nileowner"


def test_main_window_cozy_register_checkout_date_filter_and_pdf_receipt(
    qapp, isolated_data_dir: Path, tmp_path: Path, monkeypatch
):
    db = Database(get_database_path())
    window = MainWindow(db, qapp)
    _setup_owner_and_sign_in(window, db, business_name="Forest Cup Café", currency="USD")
    monkeypatch.setattr(
        "cafe_pos.ui.main_window.ReceiptDialog.exec",
        lambda self: 1,
    )

    products = db.list_products()
    flat_white = next(p for p in products if p.name == "Flat White")
    croissant = next(p for p in products if p.name == "Butter Croissant")

    reg = window.register_view
    # Empty state initially visible in cart
    assert reg.cart_stack.currentIndex() == 0

    reg.add_product_to_cart(flat_white)
    reg.add_product_to_cart(flat_white)
    reg.add_product_to_cart(croissant)
    assert reg.cart_stack.currentIndex() == 1

    count, total_minor = reg.cart_totals_minor()
    assert count == 3
    assert total_minor == flat_white.price_minor * 2 + croissant.price_minor
    assert reg.checkout_btn.isEnabled() is True

    # Checkout modal handles insufficient vs sufficient cash and computes exact change
    expected_change = 2000 - total_minor

    def _inspect_and_confirm(attempt: int = 0) -> None:
        dlg = qapp.activeModalWidget()
        if isinstance(dlg, CheckoutPaymentDialog):
            dlg.cash_edit.setText("5.00")
            assert dlg.confirm_btn.isEnabled() is False
            dlg.cash_edit.setText("20.00")
            assert dlg.confirm_btn.isEnabled() is True
            assert dlg.cash_change_val_lbl.text() == f"${expected_change / 100:.2f}"
            dlg.confirm_btn.click()
        elif attempt < 30:
            QTimer.singleShot(10, lambda: _inspect_and_confirm(attempt + 1))

    QTimer.singleShot(0, _inspect_and_confirm)
    reg.trigger_checkout()

    # Cart cleared & back to empty cart illustration
    assert len(reg.cart) == 0
    assert reg.cart_stack.currentIndex() == 0
    orders = db.list_orders()
    assert len(orders) == 1
    assert orders[0].order_number == "ORD-1001"
    assert orders[0].cashier_name_snapshot == "Owner Admin"

    # Sales history table + date filter chips work seamlessly
    window.switch_view(2)
    assert window.sales_view.table.rowCount() == 1
    assert window.sales_view.table.columnCount() == 6
    assert window.sales_view.table.item(0, 2).text() == "Owner Admin"
    window.sales_view._set_date_filter("today")
    assert window.sales_view.table.rowCount() == 1

    # Verify plain-text receipt & PDF export
    full_order = db.get_order(orders[0].id)
    assert full_order is not None
    txt = build_receipt_plain_text(full_order)
    assert "Forest Cup Café" in txt
    assert "ORD-1001" in txt
    assert "Cashier:" in txt and "Owner Admin" in txt
    assert "Flat White" in txt

    pdf_out = tmp_path / "receipt.pdf"
    export_receipt_pdf(pdf_out, full_order, db.get_settings())
    assert pdf_out.exists()
    assert pdf_out.read_bytes().startswith(b"%PDF-")

    window.close()


def test_product_editor_image_selection_persists_offline_across_relaunch(
    qapp, isolated_data_dir: Path
):
    db = Database(get_database_path())
    saved_cfg, _admin = db.initialize_owner_and_business(
        business_cfg=BusinessConfig(business_name="Offline Img Café"),
        admin_username="owner",
        admin_password="OwnerStrongPassword!1",
    )
    admin_logged = db.authenticate("owner", "OwnerStrongPassword!1")
    db.set_session_actor(admin_logged)

    dlg = ProductEditorDialog(saved_cfg, db.list_categories(), product=None)
    dlg.name_edit.setText("Iced Matcha Latte")
    dlg.category_combo.setCurrentText("Tea & Matcha")
    dlg.price_edit.setText("5.75")

    fake_result = ImageSearchResult(
        id="ov-matcha-1",
        title="Matcha Latte Cup",
        thumbnail_url="https://example.org/thumb.jpg",
        image_url="https://example.org/full.jpg",
        source_provider="openverse",
        creator="Tea Lover",
        creator_url="",
        license_code="CC0",
        license_url="",
        attribution="Photo by Tea Lover (CC0)",
        landing_url="https://openverse.org/image/ov-matcha-1",
    )
    sample_jpg = _sample_jpeg_bytes((114, 145, 123))

    # Populate gallery & simulate confirmed image download
    dlg._on_search_results("Iced Matcha Latte", [fake_result])
    assert len(dlg._search_results) == 1
    dlg._on_image_downloaded(fake_result, sample_jpg)
    dlg._set_fit_mode("contain")
    dlg._on_save()

    assert dlg.result() == QDialog.Accepted
    assert dlg.result_name == "Iced Matcha Latte"
    assert dlg.result_price_minor == 575
    assert dlg.result_image_filename.startswith("prod_")
    assert dlg.result_image_source == "openverse"
    assert (get_product_images_dir() / dlg.result_image_filename).exists()

    created = db.add_product(
        dlg.result_name,
        dlg.result_category,
        dlg.result_price_minor,
        image_filename=dlg.result_image_filename,
        image_source=dlg.result_image_source,
        image_attribution=dlg.result_image_attribution,
        image_license=dlg.result_image_license,
        image_source_url=dlg.result_image_source_url,
    )

    # Simulate app restart completely offline -> new Database + MainWindow login loads cached image
    db2 = Database(get_database_path())
    window2 = MainWindow(db2, qapp)
    reauth = db2.authenticate("owner", "OwnerStrongPassword!1")
    window2.login_user(reauth)
    window2.switch_view(1)
    assert any(p.image_filename == created.image_filename for p in window2.products_view._products)
    window2.close()


def test_product_sync_and_white_label_rebrand_without_restart(
    qapp, isolated_data_dir: Path
):
    db = Database(get_database_path())
    window = MainWindow(db, qapp)
    _setup_owner_and_sign_in(window, db)

    latte = db.add_product("Honey Oat Latte", "Specialty", 575)
    window.products_view.refresh()
    window._on_products_changed()

    window.register_view.add_product_to_cart(latte)
    assert window.register_view.cart[latte.id].unit_price_minor == 575

    # Edit product price in DB and sync active cart
    db.update_product(latte.id, "Honey Oat Latte Large", "Specialty", 650)
    window._on_products_changed()
    assert window.register_view.cart[latte.id].name == "Honey Oat Latte Large"
    assert window.register_view.cart[latte.id].unit_price_minor == 650

    # Delete product removes it from active cart cleanly
    db.delete_product(latte.id)
    window._on_products_changed()
    assert latte.id not in window.register_view.cart

    # Live white-label branding + custom logo update + image provider update
    logo_bytes = get_asset_path("default_logo.png").read_bytes()
    window.settings_view.name_edit.setText("Harbor Roast Coffee")
    window.settings_view._set_accent("#1E3A5F")
    window.settings_view._draft_logo_blob = logo_bytes
    window.settings_view._draft_logo_ext = ".png"
    updated_cfg = db.save_settings(
        BusinessConfig(
            business_name="Harbor Roast Coffee",
            accent_color="#1E3A5F",
            currency="EUR",
            address="8 Harbor Pier",
            phone="+31 20 555 0199",
            logo_blob=logo_bytes,
            logo_ext=".png",
            setup_completed=True,
            image_provider="openverse",
        )
    )
    window._on_settings_saved(updated_cfg)

    assert "Harbor Roast Coffee" in window.windowTitle()
    assert window.brand_title_lbl.text() == "Harbor Roast Coffee"
    assert "EUR" in window.brand_sub_lbl.text()

    # Check responsive header overflows secondary tabs cleanly when resized down to compact 910x490
    window.show()
    window.resize(910, 490)
    qapp.processEvents()
    assert window.nav_overflow_btn.isVisible() is True
    assert len(window.nav_overflow_menu.actions()) == 4
    window.resize(1260, 780)
    qapp.processEvents()
    assert window.nav_overflow_btn.isVisible() is False
    assert window.nav_buttons[2].text() == "Sales History"

    espresso = db.list_products()[0]
    order = db.create_order(
        [CartLine(product_id=espresso.id, name=espresso.name, unit_price_minor=espresso.price_minor, quantity=1)],
        espresso.price_minor,
    )
    doc = build_receipt_document(order, updated_cfg)
    assert "Harbor Roast Coffee" in doc.toHtml()
    assert "logo://business" in doc.toHtml()

    window.close()


def test_boutique_category_cards_dynamic_stepper_and_reports_view(
    qapp, isolated_data_dir: Path, monkeypatch
):
    db = Database(get_database_path())
    window = MainWindow(db, qapp)
    _setup_owner_and_sign_in(window, db)
    monkeypatch.setattr(
        "cafe_pos.ui.main_window.ReceiptDialog.exec",
        lambda self: 1,
    )

    reg = window.register_view
    assert get_bundled_product_asset_path("Flat White") is not None
    assert reg.orders_chip.text() == "Orders: 0"
    assert not hasattr(window, "orders_chip")

    # Switch category card to Bakery -> filters cards strictly to Bakery items
    reg._select_category("Bakery")
    assert len(reg._last_products) == 2
    assert "2 items" in reg.catalog_count_badge.text()

    # Clicking [ + ] transitions dynamically from 0 to 1, 2, back down to 0
    bakery_card = next(iter(reg._product_cards.values()))
    bakery_pid = bakery_card.product_id
    assert bakery_card.qty_control.quantity == 0
    assert bakery_card.quantity == 0

    bakery_card.qty_control.click_plus()
    assert reg.cart_totals_minor()[0] == 1
    assert bakery_card.quantity == 1
    assert bakery_card.qty_control.quantity == 1

    bakery_card.qty_control.click_plus()
    assert reg.cart_totals_minor()[0] == 2
    assert bakery_card.qty_control.quantity == 2

    bakery_card.qty_control.click_minus()
    assert reg.cart_totals_minor()[0] == 1
    assert bakery_card.qty_control.quantity == 1

    # Switch category away and back: quantity state is preserved from active cart
    reg._select_category("All")
    assert reg._product_cards[bakery_pid].quantity == 1
    assert reg._product_cards[bakery_pid].qty_control.quantity == 1

    # Search filter also preserves bidirectional card <-> cart quantity
    reg.search_input.setText(bakery_card._product_name if hasattr(bakery_card, "_product_name") else "Croissant")
    assert len(reg._product_cards) >= 1
    if bakery_pid in reg._product_cards:
        assert reg._product_cards[bakery_pid].quantity == 1
    reg.search_input.clear()

    # Complete order and check Register orders chip + Reports view SQLite data updates live
    total_minor = reg.cart_totals_minor()[1]
    _schedule_confirm_modal_cash_checkout(qapp, f"{total_minor / 100:.2f}")
    reg.trigger_checkout()

    # Card control returns cleanly to compact [ + ] button (quantity 0) after checkout
    assert reg._product_cards[bakery_pid].quantity == 0
    assert reg._product_cards[bakery_pid].qty_control.quantity == 0

    assert reg.orders_chip.text() == "Orders: 1"
    window.switch_view(3)
    rep = window.reports_view
    assert rep.kpi_revenue.value_lbl.text() != "$0.00"
    assert rep.kpi_items.value_lbl.text() == "1"
    assert rep.recent_table.rowCount() == 1

    window.close()
