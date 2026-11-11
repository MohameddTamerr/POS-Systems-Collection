from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    BusinessConfig,
    DEFAULT_ACCENT_COLOR,
    DEFAULT_BUSINESS_NAME,
    DEFAULT_CURRENCY,
    validate_currency,
    validate_hex_color,
)


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    category: str
    price_minor: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CartLine:
    product_id: int
    name: str
    unit_price_minor: int
    quantity: int

    @property
    def line_total_minor(self) -> int:
        return int(self.unit_price_minor) * int(self.quantity)


@dataclass(frozen=True)
class OrderItemRecord:
    product_id: int | None
    product_name: str
    unit_price_minor: int
    quantity: int
    line_total_minor: int


@dataclass(frozen=True)
class OrderRecord:
    id: int
    order_number: str
    created_at: str
    currency: str
    subtotal_minor: int
    total_minor: int
    cash_received_minor: int
    change_minor: int
    business_name: str
    business_address: str
    business_phone: str
    items: list[OrderItemRecord]


@dataclass(frozen=True)
class OrderSummary:
    id: int
    order_number: str
    created_at: str
    currency: str
    item_count: int
    total_minor: int
    cash_received_minor: int
    change_minor: int


DEFAULT_PRODUCTS: list[tuple[str, str, int]] = [
    ("Double Espresso", "Espresso", 325),
    ("Flat White", "Espresso", 450),
    ("Classic Cappuccino", "Espresso", 450),
    ("Caramel Latte", "Espresso", 500),
    ("Iced Americano", "Cold Drinks", 400),
    ("Iced Vanilla Latte", "Cold Drinks", 525),
    ("Ceremonial Matcha", "Tea & Matcha", 550),
    ("Spiced Chai Latte", "Tea & Matcha", 475),
    ("Butter Croissant", "Bakery", 375),
    ("Chocolate Muffin", "Bakery", 400),
    ("Turkey Pesto Panini", "Kitchen", 850),
    ("House Blend Beans (250g)", "Retail", 1400),
]

REQUIRED_TABLES = {"app_meta", "business_settings", "products", "orders", "order_items"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Thread-safe local SQLite persistence for Café POS."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = DELETE;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS business_settings (
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

                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    price_minor INTEGER NOT NULL CHECK (price_minor >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_number TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    subtotal_minor INTEGER NOT NULL CHECK (subtotal_minor >= 0),
                    total_minor INTEGER NOT NULL CHECK (total_minor >= 0),
                    cash_received_minor INTEGER NOT NULL CHECK (cash_received_minor >= 0),
                    change_minor INTEGER NOT NULL CHECK (change_minor >= 0),
                    business_name TEXT NOT NULL,
                    business_address TEXT NOT NULL DEFAULT '',
                    business_phone TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    product_id INTEGER,
                    product_name TEXT NOT NULL,
                    unit_price_minor INTEGER NOT NULL CHECK (unit_price_minor >= 0),
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    line_total_minor INTEGER NOT NULL CHECK (line_total_minor >= 0)
                );
                """
            )

            conn.execute(
                "INSERT OR IGNORE INTO app_meta (key, value) VALUES ('schema_version', '1');"
            )

            existing_settings = conn.execute(
                "SELECT id FROM business_settings WHERE id = 1;"
            ).fetchone()
            if existing_settings is None:
                conn.execute(
                    """
                    INSERT INTO business_settings (
                        id, business_name, accent_color, currency, address, phone,
                        logo_blob, logo_ext, setup_completed, next_order_seq
                    ) VALUES (1, ?, ?, ?, '', '', X'', '', 0, 1001);
                    """,
                    (DEFAULT_BUSINESS_NAME, DEFAULT_ACCENT_COLOR, DEFAULT_CURRENCY),
                )

            seeded_row = conn.execute(
                "SELECT value FROM app_meta WHERE key = 'seeded_defaults';"
            ).fetchone()
            if seeded_row is None:
                now = _utc_now_iso()
                conn.executemany(
                    """
                    INSERT INTO products (name, category, price_minor, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    [(name, cat, price, now, now) for name, cat, price in DEFAULT_PRODUCTS],
                )
                conn.execute(
                    "INSERT INTO app_meta (key, value) VALUES ('seeded_defaults', '1');"
                )

    def get_settings(self) -> BusinessConfig:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM business_settings WHERE id = 1;").fetchone()
            if row is None:
                return BusinessConfig()
            return BusinessConfig(
                business_name=row["business_name"],
                accent_color=validate_hex_color(row["accent_color"]),
                currency=validate_currency(row["currency"]),
                address=row["address"] or "",
                phone=row["phone"] or "",
                logo_blob=bytes(row["logo_blob"] or b""),
                logo_ext=row["logo_ext"] or "",
                setup_completed=bool(row["setup_completed"]),
            )

    def save_settings(self, cfg: BusinessConfig) -> BusinessConfig:
        name = (cfg.business_name or "").strip()[:60]
        if not name:
            raise ValueError("Business name cannot be empty.")
        clean = BusinessConfig(
            business_name=name,
            accent_color=validate_hex_color(cfg.accent_color),
            currency=validate_currency(cfg.currency),
            address=(cfg.address or "").strip()[:140],
            phone=(cfg.phone or "").strip()[:40],
            logo_blob=bytes(cfg.logo_blob or b""),
            logo_ext=(cfg.logo_ext or "").strip()[:10],
            setup_completed=bool(cfg.setup_completed),
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE business_settings
                SET business_name = ?,
                    accent_color = ?,
                    currency = ?,
                    address = ?,
                    phone = ?,
                    logo_blob = ?,
                    logo_ext = ?,
                    setup_completed = ?
                WHERE id = 1;
                """,
                (
                    clean.business_name,
                    clean.accent_color,
                    clean.currency,
                    clean.address,
                    clean.phone,
                    sqlite3.Binary(clean.logo_blob),
                    clean.logo_ext,
                    1 if clean.setup_completed else 0,
                ),
            )
        return clean

    def list_categories(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM products ORDER BY category COLLATE NOCASE ASC;"
            ).fetchall()
            return [row["category"] for row in rows]

    def list_products(self, search: str = "", category: str = "") -> list[Product]:
        clauses: list[str] = []
        params: list[object] = []

        if category and category != "All":
            clauses.append("category = ?")
            params.append(category)

        q = (search or "").strip()
        if q:
            clauses.append("(name LIKE ? OR category LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT id, name, category, price_minor, created_at, updated_at
            FROM products
            {where_sql}
            ORDER BY category COLLATE NOCASE ASC, name COLLATE NOCASE ASC;
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [
                Product(
                    id=int(r["id"]),
                    name=r["name"],
                    category=r["category"],
                    price_minor=int(r["price_minor"]),
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ]

    def add_product(self, name: str, category: str, price_minor: int) -> Product:
        clean_name = (name or "").strip()[:80]
        clean_cat = (category or "").strip()[:40]
        if not clean_name:
            raise ValueError("Product name is required.")
        if not clean_cat:
            raise ValueError("Category is required.")
        if not isinstance(price_minor, int) or price_minor < 0:
            raise ValueError("Price must be a non-negative integer amount.")

        now = _utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO products (name, category, price_minor, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (clean_name, clean_cat, price_minor, now, now),
            )
            product_id = int(cur.lastrowid)
        return Product(
            id=product_id,
            name=clean_name,
            category=clean_cat,
            price_minor=price_minor,
            created_at=now,
            updated_at=now,
        )

    def update_product(self, product_id: int, name: str, category: str, price_minor: int) -> Product:
        clean_name = (name or "").strip()[:80]
        clean_cat = (category or "").strip()[:40]
        if not clean_name:
            raise ValueError("Product name is required.")
        if not clean_cat:
            raise ValueError("Category is required.")
        if not isinstance(price_minor, int) or price_minor < 0:
            raise ValueError("Price must be a non-negative integer amount.")

        now = _utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE products
                SET name = ?, category = ?, price_minor = ?, updated_at = ?
                WHERE id = ?;
                """,
                (clean_name, clean_cat, price_minor, now, int(product_id)),
            )
            if cur.rowcount == 0:
                raise ValueError(f"Product ID {product_id} was not found.")
            row = conn.execute(
                "SELECT id, name, category, price_minor, created_at, updated_at FROM products WHERE id = ?;",
                (int(product_id),),
            ).fetchone()
        return Product(
            id=int(row["id"]),
            name=row["name"],
            category=row["category"],
            price_minor=int(row["price_minor"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def delete_product(self, product_id: int) -> None:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM products WHERE id = ?;", (int(product_id),))
            if cur.rowcount == 0:
                raise ValueError(f"Product ID {product_id} was not found.")

    def restore_sample_products(self) -> None:
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute("DELETE FROM products;")
            conn.executemany(
                """
                INSERT INTO products (name, category, price_minor, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                [(name, cat, price, now, now) for name, cat, price in DEFAULT_PRODUCTS],
            )

    def create_order(self, lines: list[CartLine], cash_received_minor: int) -> OrderRecord:
        if not lines:
            raise ValueError("Cannot complete an empty order.")
        if not isinstance(cash_received_minor, int) or cash_received_minor < 0:
            raise ValueError("Cash received must be a valid non-negative amount.")

        subtotal_minor = 0
        validated_lines: list[CartLine] = []
        for line in lines:
            if line.quantity <= 0:
                raise ValueError(f"Quantity for '{line.name}' must be positive.")
            if line.unit_price_minor < 0:
                raise ValueError(f"Price for '{line.name}' cannot be negative.")
            subtotal_minor += line.line_total_minor
            validated_lines.append(line)

        total_minor = subtotal_minor
        if cash_received_minor < total_minor:
            raise ValueError("Cash received is less than the order total.")

        change_minor = cash_received_minor - total_minor
        now = _utc_now_iso()

        with self._connect() as conn:
            settings_row = conn.execute(
                "SELECT business_name, address, phone, currency, next_order_seq FROM business_settings WHERE id = 1;"
            ).fetchone()
            seq = int(settings_row["next_order_seq"])
            order_number = f"ORD-{seq}"
            currency = validate_currency(settings_row["currency"])
            biz_name = settings_row["business_name"]
            biz_addr = settings_row["address"] or ""
            biz_phone = settings_row["phone"] or ""

            conn.execute(
                "UPDATE business_settings SET next_order_seq = ? WHERE id = 1;",
                (seq + 1,),
            )

            cur = conn.execute(
                """
                INSERT INTO orders (
                    order_number, created_at, currency, subtotal_minor, total_minor,
                    cash_received_minor, change_minor, business_name, business_address, business_phone
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    order_number,
                    now,
                    currency,
                    subtotal_minor,
                    total_minor,
                    cash_received_minor,
                    change_minor,
                    biz_name,
                    biz_addr,
                    biz_phone,
                ),
            )
            order_id = int(cur.lastrowid)

            item_records: list[OrderItemRecord] = []
            for line in validated_lines:
                line_total = line.line_total_minor
                conn.execute(
                    """
                    INSERT INTO order_items (
                        order_id, product_id, product_name, unit_price_minor, quantity, line_total_minor
                    ) VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (
                        order_id,
                        line.product_id,
                        line.name,
                        line.unit_price_minor,
                        line.quantity,
                        line_total,
                    ),
                )
                item_records.append(
                    OrderItemRecord(
                        product_id=line.product_id,
                        product_name=line.name,
                        unit_price_minor=line.unit_price_minor,
                        quantity=line.quantity,
                        line_total_minor=line_total,
                    )
                )

        return OrderRecord(
            id=order_id,
            order_number=order_number,
            created_at=now,
            currency=currency,
            subtotal_minor=subtotal_minor,
            total_minor=total_minor,
            cash_received_minor=cash_received_minor,
            change_minor=change_minor,
            business_name=biz_name,
            business_address=biz_addr,
            business_phone=biz_phone,
            items=item_records,
        )

    def list_orders(self, search: str = "") -> list[OrderSummary]:
        q = (search or "").strip()
        params: list[object] = []
        where_sql = ""
        if q:
            where_sql = """
                WHERE o.order_number LIKE ?
                   OR EXISTS (
                       SELECT 1 FROM order_items oi
                       WHERE oi.order_id = o.id AND oi.product_name LIKE ?
                   )
            """
            like = f"%{q}%"
            params = [like, like]

        sql = f"""
            SELECT
                o.id,
                o.order_number,
                o.created_at,
                o.currency,
                o.total_minor,
                o.cash_received_minor,
                o.change_minor,
                COALESCE(SUM(oi.quantity), 0) AS item_count
            FROM orders o
            LEFT JOIN order_items oi ON oi.order_id = o.id
            {where_sql}
            GROUP BY o.id
            ORDER BY o.id DESC;
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [
                OrderSummary(
                    id=int(r["id"]),
                    order_number=r["order_number"],
                    created_at=r["created_at"],
                    currency=r["currency"],
                    item_count=int(r["item_count"]),
                    total_minor=int(r["total_minor"]),
                    cash_received_minor=int(r["cash_received_minor"]),
                    change_minor=int(r["change_minor"]),
                )
                for r in rows
            ]

    def get_order(self, order_id: int) -> OrderRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM orders WHERE id = ?;", (int(order_id),)).fetchone()
            if row is None:
                return None
            item_rows = conn.execute(
                """
                SELECT product_id, product_name, unit_price_minor, quantity, line_total_minor
                FROM order_items
                WHERE order_id = ?
                ORDER BY id ASC;
                """,
                (int(order_id),),
            ).fetchall()
            items = [
                OrderItemRecord(
                    product_id=int(ir["product_id"]) if ir["product_id"] is not None else None,
                    product_name=ir["product_name"],
                    unit_price_minor=int(ir["unit_price_minor"]),
                    quantity=int(ir["quantity"]),
                    line_total_minor=int(ir["line_total_minor"]),
                )
                for ir in item_rows
            ]
            return OrderRecord(
                id=int(row["id"]),
                order_number=row["order_number"],
                created_at=row["created_at"],
                currency=row["currency"],
                subtotal_minor=int(row["subtotal_minor"]),
                total_minor=int(row["total_minor"]),
                cash_received_minor=int(row["cash_received_minor"]),
                change_minor=int(row["change_minor"]),
                business_name=row["business_name"],
                business_address=row["business_address"] or "",
                business_phone=row["business_phone"] or "",
                items=items,
            )

    def backup_to_file(self, dest_path: Path) -> None:
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.resolve() == self.db_path.resolve():
            raise ValueError("Backup destination must be different from the live database file.")
        with self._connect() as src_conn:
            dest_conn = sqlite3.connect(str(dest))
            try:
                src_conn.backup(dest_conn)
            finally:
                dest_conn.close()

    @staticmethod
    def validate_backup_file(src_path: Path) -> None:
        src = Path(src_path)
        if not src.exists() or not src.is_file():
            raise ValueError("Backup file does not exist.")
        if src.stat().st_size < 512:
            raise ValueError("Selected file is not a valid SQLite Café POS backup.")

        try:
            conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise ValueError("Unable to open selected file as a SQLite database.") from exc

        try:
            integrity = conn.execute("PRAGMA integrity_check;").fetchone()
            if not integrity or integrity[0] != "ok":
                raise ValueError("Backup database failed SQLite integrity verification.")
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table';"
                ).fetchall()
            }
            missing = REQUIRED_TABLES - tables
            if missing:
                raise ValueError("Selected SQLite file is not a valid Café POS database backup.")
            settings_count = conn.execute("SELECT COUNT(*) FROM business_settings;").fetchone()[0]
            if settings_count != 1:
                raise ValueError("Backup database is missing business configuration.")
        except sqlite3.Error as exc:
            raise ValueError("Invalid or corrupted SQLite backup file.") from exc
        finally:
            conn.close()

    def restore_from_file(self, src_path: Path) -> None:
        src = Path(src_path)
        if src.resolve() == self.db_path.resolve():
            raise ValueError("Cannot restore the active database onto itself.")
        self.validate_backup_file(src)

        src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            with self._connect() as dst_conn:
                src_conn.backup(dst_conn)
        finally:
            src_conn.close()
        self._init_schema()

    def factory_reset(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                DELETE FROM order_items;
                DELETE FROM orders;
                DELETE FROM products;
                DELETE FROM business_settings;
                DELETE FROM app_meta;
                """
            )
        self._init_schema()

