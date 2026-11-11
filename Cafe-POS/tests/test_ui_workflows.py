from pathlib import Path

from cafe_pos.config import BusinessConfig
from cafe_pos.db import Database
from cafe_pos.paths import get_asset_path, get_database_path
from cafe_pos.receipts import build_receipt_document, build_receipt_plain_text, export_receipt_pdf
from cafe_pos.ui.main_window import MainWindow
from cafe_pos.ui.setup_dialog import SetupDialog


def test_setup_dialog_customization(qapp, isolated_data_dir: Path):
    db = Database(get_database_path())
    dlg = SetupDialog(db.get_settings(), is_first_launch=True)
    dlg.name_edit.setText("Nile Brew Roasters")
    idx = dlg.currency_combo.findData("EGP")
    dlg.currency_combo.setCurrentIndex(idx)
    dlg._set_accent_color("#1E3A5F")
    dlg.address_edit.setText("55 Zamalek St")
    dlg.phone_edit.setText("+20 123 456 789")
    dlg._on_save()

    assert dlg.result_config is not None
    saved = db.save_settings(dlg.result_config)
    assert saved.business_name == "Nile Brew Roasters"
    assert saved.currency == "EGP"
    assert saved.accent_color == "#1E3A5F"
    assert saved.setup_completed is True


def test_main_window_cashier_workflow_and_pdf_receipt(
    qapp, isolated_data_dir: Path, tmp_path: Path, monkeypatch
):
    db = Database(get_database_path())
    db.save_settings(
        BusinessConfig(
            business_name="Forest Cup Café",
            accent_color="#1B4332",
            currency="USD",
            address="100 Oak Ave",
            phone="555-0100",
            setup_completed=True,
        )
    )

    window = MainWindow(db, qapp)
    # Prevent ReceiptDialog.exec() from blocking offscreen test
    monkeypatch.setattr(
        "cafe_pos.ui.main_window.ReceiptDialog.exec",
        lambda self: 1,
    )

    products = db.list_products()
    flat_white = next(p for p in products if p.name == "Flat White")
    croissant = next(p for p in products if p.name == "Butter Croissant")

    reg = window.register_view
    reg.add_product_to_cart(flat_white)
    reg.add_product_to_cart(flat_white)
    reg.add_product_to_cart(croissant)

    count, total_minor = reg.cart_totals_minor()
    assert count == 3
    assert total_minor == flat_white.price_minor * 2 + croissant.price_minor

    # Insufficient cash prevents checkout
    reg.cash_input.setText("5.00")
    assert reg.checkout_btn.isEnabled() is False

    # Sufficient cash enables checkout & computes exact change
    reg.cash_input.setText("20.00")
    assert reg.checkout_btn.isEnabled() is True
    expected_change = 2000 - total_minor
    assert reg.change_val_lbl.text() == f"${expected_change / 100:.2f}"

    reg.trigger_checkout()

    # Cart cleared & order recorded in SQLite & visible in Sales History
    assert len(reg.cart) == 0
    orders = db.list_orders()
    assert len(orders) == 1
    assert orders[0].order_number == "ORD-1001"

    window.switch_view(2)
    assert window.sales_view.table.rowCount() == 1

    # Verify plain-text receipt & PDF export
    full_order = db.get_order(orders[0].id)
    assert full_order is not None
    txt = build_receipt_plain_text(full_order)
    assert "Forest Cup Café" in txt
    assert "ORD-1001" in txt
    assert "Flat White" in txt

    pdf_out = tmp_path / "receipt.pdf"
    export_receipt_pdf(pdf_out, full_order, db.get_settings())
    assert pdf_out.exists()
    assert pdf_out.read_bytes().startswith(b"%PDF-")

    window.close()


def test_product_sync_and_white_label_rebrand_without_restart(
    qapp, isolated_data_dir: Path
):
    db = Database(get_database_path())
    window = MainWindow(db, qapp)

    # Add a product via DB + ProductsView refresh & sync
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

    # Live white-label branding + custom logo update
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
        )
    )
    window._on_settings_saved(updated_cfg)

    assert "Harbor Roast Coffee" in window.windowTitle()
    assert window.brand_title_lbl.text() == "Harbor Roast Coffee"
    assert "EUR" in window.brand_sub_lbl.text()

    # Receipt document renders custom logo + updated branding
    espresso = db.list_products()[0]
    from cafe_pos.db import CartLine

    order = db.create_order(
        [CartLine(product_id=espresso.id, name=espresso.name, unit_price_minor=espresso.price_minor, quantity=1)],
        espresso.price_minor,
    )
    doc = build_receipt_document(order, updated_cfg)
    assert "Harbor Roast Coffee" in doc.toHtml()
    assert "logo://business" in doc.toHtml()

    window.close()


