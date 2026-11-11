from pathlib import Path

import pytest

from cafe_pos.config import BusinessConfig, read_and_validate_logo
from cafe_pos.db import CartLine, Database


def test_database_seeds_defaults_and_persists_across_reopen(tmp_path: Path):
    db_path = tmp_path / "cafe_pos.db"
    db = Database(db_path)

    products = db.list_products()
    assert len(products) == 12
    cfg = db.get_settings()
    assert cfg.setup_completed is False
    assert cfg.accent_color == "#1B4332"

    # Modify settings and add product
    saved_cfg = db.save_settings(
        BusinessConfig(
            business_name="Cedar Espresso Bar",
            accent_color="#4A2C2A",
            currency="EUR",
            address="12 Corniche St",
            phone="+20 100 000 1111",
            logo_blob=b"\x89PNG\r\n\x1a\nfake",
            logo_ext=".png",
            setup_completed=True,
        )
    )
    assert saved_cfg.business_name == "Cedar Espresso Bar"
    created = db.add_product("Pistachio Latte", "Specialty", 590)

    # Simulate application restart with new Database instance
    db_reloaded = Database(db_path)
    cfg2 = db_reloaded.get_settings()
    assert cfg2.business_name == "Cedar Espresso Bar"
    assert cfg2.accent_color == "#4A2C2A"
    assert cfg2.currency == "EUR"
    assert cfg2.setup_completed is True
    assert cfg2.logo_blob == b"\x89PNG\r\n\x1a\nfake"

    found = db_reloaded.list_products(search="Pistachio")
    assert len(found) == 1
    assert found[0].id == created.id
    assert found[0].price_minor == 590


def test_product_crud_and_search(tmp_path: Path):
    db = Database(tmp_path / "cafe.db")
    item = db.add_product("Oat Cortado", "Espresso", 475)
    updated = db.update_product(item.id, "Almond Cortado", "Specialty", 525)
    assert updated.name == "Almond Cortado"
    assert updated.category == "Specialty"
    assert updated.price_minor == 525

    assert len(db.list_products(category="Specialty")) == 1
    db.delete_product(item.id)
    assert len(db.list_products(search="Almond Cortado")) == 0


def test_order_creation_and_cash_validation(tmp_path: Path):
    db = Database(tmp_path / "cafe.db")
    products = db.list_products()
    p1 = products[0]
    p2 = products[1]

    lines = [
        CartLine(product_id=p1.id, name=p1.name, unit_price_minor=p1.price_minor, quantity=2),
        CartLine(product_id=p2.id, name=p2.name, unit_price_minor=p2.price_minor, quantity=1),
    ]
    expected_total = p1.price_minor * 2 + p2.price_minor

    with pytest.raises(ValueError, match="less than"):
        db.create_order(lines, expected_total - 1)

    order = db.create_order(lines, expected_total + 500)
    assert order.order_number == "ORD-1001"
    assert order.total_minor == expected_total
    assert order.change_minor == 500
    assert len(order.items) == 2

    summaries = db.list_orders(search="ORD-1001")
    assert len(summaries) == 1
    assert summaries[0].item_count == 3


def test_backup_restore_and_validation(tmp_path: Path):
    db_path = tmp_path / "live.db"
    backup_path = tmp_path / "backup.db"
    db = Database(db_path)

    db.save_settings(
        BusinessConfig(
            business_name="Backup Test Café",
            accent_color="#1E3A5F",
            currency="GBP",
            setup_completed=True,
        )
    )
    prod = db.add_product("Unique Mint Tea", "Tea", 350)
    db.create_order(
        [CartLine(product_id=prod.id, name=prod.name, unit_price_minor=350, quantity=2)],
        1000,
    )
    db.backup_to_file(backup_path)
    assert backup_path.exists()

    # Mutate live DB via factory reset
    db.factory_reset()
    assert db.get_settings().business_name != "Backup Test Café"
    assert len(db.list_orders()) == 0

    # Reject corrupted / non-SQLite file
    bad_file = tmp_path / "corrupt.db"
    bad_file.write_text("not a sqlite database", encoding="utf-8")
    with pytest.raises(ValueError):
        db.restore_from_file(bad_file)

    # Restore from valid backup
    db.restore_from_file(backup_path)
    assert db.get_settings().business_name == "Backup Test Café"
    assert db.get_settings().currency == "GBP"
    assert len(db.list_orders()) == 1
    assert len(db.list_products(search="Unique Mint Tea")) == 1


def test_logo_validation(tmp_path: Path):
    bad_ext = tmp_path / "logo.txt"
    bad_ext.write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        read_and_validate_logo(bad_ext)

    too_big = tmp_path / "huge.png"
    too_big.write_bytes(b"0" * (2 * 1024 * 1024 + 10))
    with pytest.raises(ValueError, match="too large"):
        read_and_validate_logo(too_big)

