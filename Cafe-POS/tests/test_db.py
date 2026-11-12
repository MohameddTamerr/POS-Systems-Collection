import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cafe_pos.config import (
    BusinessConfig,
    DEFAULT_ACCENT_COLOR,
    read_and_validate_logo,
)
from cafe_pos.db import CartLine, Database


def test_database_seeds_cozy_defaults_and_persists_across_reopen(tmp_path: Path):
    db_path = tmp_path / "cafe_pos.db"
    db = Database(db_path)

    products = db.list_products()
    assert len(products) == 12
    cfg = db.get_settings()
    assert cfg.setup_completed is False
    assert cfg.accent_color == DEFAULT_ACCENT_COLOR == "#005B37"
    assert cfg.image_provider == "openverse"
    assert cfg.pexels_api_key == ""

    # Modify settings and add product with photo metadata
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
            image_provider="pexels",
            pexels_api_key="user-key-demo",
        )
    )
    assert saved_cfg.business_name == "Cedar Espresso Bar"
    created = db.add_product(
        "Pistachio Latte",
        "Specialty",
        590,
        image_filename="prod_deadbeef.jpg",
        image_source="openverse",
        image_attribution="Photo by Café Creator (CC0)",
        image_license="CC0",
        image_source_url="https://openverse.org/image/demo",
    )

    # Simulate application restart with new Database instance
    db_reloaded = Database(db_path)
    cfg2 = db_reloaded.get_settings()
    assert cfg2.business_name == "Cedar Espresso Bar"
    assert cfg2.accent_color == "#4A2C2A"
    assert cfg2.currency == "EUR"
    assert cfg2.setup_completed is True
    assert cfg2.logo_blob == b"\x89PNG\r\n\x1a\nfake"
    assert cfg2.image_provider == "pexels"
    assert cfg2.pexels_api_key == "user-key-demo"

    found = db_reloaded.list_products(search="Pistachio")
    assert len(found) == 1
    assert found[0].id == created.id
    assert found[0].price_minor == 590
    assert found[0].image_filename == "prod_deadbeef.jpg"
    assert found[0].image_license == "CC0"
    assert db_reloaded.list_referenced_image_filenames() == {"prod_deadbeef.jpg"}


def test_non_destructive_v1_to_v2_migration_preserves_existing_data(tmp_path: Path):
    """Existing v1 databases preserve custom accent color (#1B4332), menu items, and sales."""
    v1_path = tmp_path / "legacy_v1.db"
    with sqlite3.connect(str(v1_path)) as conn:
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
                setup_completed INTEGER NOT NULL DEFAULT 0,
                next_order_seq INTEGER NOT NULL DEFAULT 1001
            );
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                price_minor INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
                business_phone TEXT NOT NULL DEFAULT ''
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

            INSERT INTO app_meta (key, value) VALUES ('seeded_defaults', '1');
            INSERT INTO business_settings (
                id, business_name, accent_color, currency, address, phone,
                logo_blob, logo_ext, setup_completed, next_order_seq
            ) VALUES (1, 'Heritage Coffee Co.', '#1B4332', 'GBP', '9 King St', '020-5555', X'', '', 1, 1042);

            INSERT INTO products (id, name, category, price_minor, created_at, updated_at)
            VALUES (1, 'Turkish Coffee', 'Espresso', 420, '2026-01-05T10:00:00+00:00', '2026-01-05T10:00:00+00:00');

            INSERT INTO orders (
                id, order_number, created_at, currency, subtotal_minor, total_minor,
                cash_received_minor, change_minor, business_name, business_address, business_phone
            ) VALUES (
                1, 'ORD-1041', '2026-01-05T10:15:00+00:00', 'GBP', 420, 420, 500, 80,
                'Heritage Coffee Co.', '9 King St', '020-5555'
            );
            INSERT INTO order_items (order_id, product_id, product_name, unit_price_minor, quantity, line_total_minor)
            VALUES (1, 1, 'Turkish Coffee', 420, 1, 420);
            """
        )

    db = Database(v1_path)
    cfg = db.get_settings()
    assert cfg.business_name == "Heritage Coffee Co."
    assert cfg.accent_color == "#1B4332"
    assert cfg.currency == "GBP"
    assert cfg.setup_completed is True
    assert cfg.image_provider == "openverse"

    prods = db.list_products()
    assert len(prods) == 1
    assert prods[0].name == "Turkish Coffee"
    assert prods[0].image_filename == ""

    orders = db.list_orders()
    assert len(orders) == 1
    assert orders[0].order_number == "ORD-1041"

    # Can write image metadata to migrated product
    updated = db.update_product(
        1,
        "Turkish Coffee",
        "Espresso",
        420,
        image_filename="prod_migrated.jpg",
        image_source="local",
    )
    assert updated.image_filename == "prod_migrated.jpg"


def test_product_crud_and_search(tmp_path: Path):
    db = Database(tmp_path / "cafe.db")
    item = db.add_product("Oat Cortado", "Espresso", 475, image_filename="prod_a.jpg")
    updated = db.update_product(item.id, "Almond Cortado", "Specialty", 525)
    assert updated.name == "Almond Cortado"
    assert updated.category == "Specialty"
    assert updated.price_minor == 525
    assert updated.image_filename == "prod_a.jpg"  # omitted image args preserve existing metadata

    cleared = db.update_product(
        item.id,
        "Almond Cortado",
        "Specialty",
        525,
        image_filename="",
        image_source="",
    )
    assert cleared.image_filename == ""

    assert len(db.list_products(category="Specialty")) == 1
    db.delete_product(item.id)
    assert len(db.list_products(search="Almond Cortado")) == 0


def test_order_creation_cash_validation_and_date_filters(tmp_path: Path):
    db_path = tmp_path / "cafe.db"
    db = Database(db_path)
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

    today_order = db.create_order(lines, expected_total + 500)
    assert today_order.order_number == "ORD-1001"
    assert today_order.total_minor == expected_total
    assert today_order.change_minor == 500

    # Insert backdated orders directly to test 'today', 'week', 'month', 'all'
    five_days_ago = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(timespec="seconds")
    forty_days_ago = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="seconds")
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO orders (
                order_number, created_at, currency, subtotal_minor, total_minor,
                cash_received_minor, change_minor, business_name
            ) VALUES ('ORD-1002', ?, 'USD', 400, 400, 500, 100, 'Cafe');
            """,
            (five_days_ago,),
        )
        conn.execute(
            """
            INSERT INTO orders (
                order_number, created_at, currency, subtotal_minor, total_minor,
                cash_received_minor, change_minor, business_name
            ) VALUES ('ORD-1003', ?, 'USD', 300, 300, 500, 200, 'Cafe');
            """,
            (forty_days_ago,),
        )

    assert len(db.list_orders(date_filter="all")) == 3
    assert [o.order_number for o in db.list_orders(date_filter="today")] == ["ORD-1001"]
    week_nums = {o.order_number for o in db.list_orders(date_filter="week")}
    assert week_nums == {"ORD-1001", "ORD-1002"}
    month_nums = {o.order_number for o in db.list_orders(date_filter="month")}
    assert month_nums == {"ORD-1001", "ORD-1002"}


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
            image_provider="openverse",
        )
    )
    prod = db.add_product(
        "Unique Mint Tea",
        "Tea & Matcha",
        350,
        image_filename="prod_mint.jpg",
        image_source="local",
    )
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
    found = db.list_products(search="Unique Mint Tea")
    assert len(found) == 1
    assert found[0].image_filename == "prod_mint.jpg"


def test_logo_validation(tmp_path: Path):
    bad_ext = tmp_path / "logo.txt"
    bad_ext.write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        read_and_validate_logo(bad_ext)

    too_big = tmp_path / "huge.png"
    too_big.write_bytes(b"0" * (2 * 1024 * 1024 + 10))
    with pytest.raises(ValueError, match="too large"):
        read_and_validate_logo(too_big)


def test_category_counts_and_sales_analytics(tmp_path: Path):
    db_path = tmp_path / "analytics.db"
    db = Database(db_path)

    counts = db.get_category_counts()
    assert sum(counts.values()) == 12
    assert counts["Espresso"] == 4
    assert db.get_order_count() == 0

    empty_week = db.get_sales_analytics("week")
    assert empty_week.order_count == 0
    assert empty_week.total_revenue_minor == 0
    assert empty_week.today_revenue_minor == 0
    assert empty_week.category_sales == []
    assert empty_week.top_products == []

    products = db.list_products()
    flat_white = next(p for p in products if p.name == "Flat White")
    croissant = next(p for p in products if p.name == "Butter Croissant")
    temp_special = db.add_product("Seasonal Tart", "Bakery", 600)

    order1 = db.create_order(
        [
            CartLine(
                product_id=flat_white.id,
                name=flat_white.name,
                unit_price_minor=flat_white.price_minor,
                quantity=2,
            ),
            CartLine(
                product_id=croissant.id,
                name=croissant.name,
                unit_price_minor=croissant.price_minor,
                quantity=1,
            ),
        ],
        2000,
    )
    order2 = db.create_order(
        [
            CartLine(
                product_id=temp_special.id,
                name=temp_special.name,
                unit_price_minor=600,
                quantity=1,
            )
        ],
        1000,
    )
    db.delete_product(temp_special.id)

    expected_rev = order1.total_minor + order2.total_minor
    summary = db.get_sales_analytics("today")
    assert db.get_order_count() == 2
    assert summary.order_count == 2
    assert summary.total_revenue_minor == expected_rev
    assert summary.today_revenue_minor == expected_rev
    assert summary.today_order_count == 2
    assert summary.total_items_sold == 4
    assert summary.average_order_minor == expected_rev // 2
    assert len(summary.daily_points) >= 1
    assert summary.top_products[0].product_name == "Flat White"
    assert summary.top_products[0].units_sold == 2

    # Deleted product with category_snapshot preserves its historical Bakery category
    cats = {cs.category: cs for cs in summary.category_sales}
    assert "Espresso" in cats
    assert "Bakery" in cats
    assert cats["Bakery"].total_minor == croissant.price_minor + 600
    assert cats["Bakery"].units_sold == 2

    # Unsnapshotted legacy deleted item (product_id NULL & category_snapshot='') resolves to 'Other'
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE order_items SET category_snapshot = '' WHERE order_id = ?;",
            (order2.id,),
        )
    summary_after = db.get_sales_analytics("today")
    cats_after = {cs.category: cs for cs in summary_after.category_sales}
    assert "Other" in cats_after
    assert cats_after["Other"].total_minor == 600
