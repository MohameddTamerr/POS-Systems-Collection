from __future__ import annotations

import gc
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from ._db_finance import (
    BreakdownPoint,
    DashboardBucketPoint,
    DatabaseFinanceMixin,
    EXPENSE_CATEGORIES,
    ExpenseRecord,
    ExtendedAnalyticsSummary,
    REFUND_STOCK_ACTIONS,
    SHIFT_MOVEMENT_TYPES,
    ShiftCashMovementRecord,
    ShiftRecord,
)
from ._db_inventory import (
    DatabaseInventoryMixin,
    INGREDIENT_UNITS,
    IngredientRecord,
    RecipeEntryRecord,
    StockMovementRecord,
    format_qty_milli,
    parse_qty_to_milli,
)
from .auth import (
    AuditEvent,
    AuthenticationError,
    AuthorizationError,
    Permission,
    ROLE_ADMIN,
    ROLE_CASHIER,
    UserAccount,
    dummy_verify,
    hash_password,
    needs_rehash,
    require_permission,
    throttle_duration_seconds,
    validate_display_name,
    validate_password_policy,
    validate_preferred_language,
    validate_role,
    validate_username,
    verify_password,
)
from .bilingual import (
    CATEGORY_GLOSSARY_AR_TO_EN,
    CATEGORY_GLOSSARY_EN_TO_AR,
    detect_primary_script,
    normalize_category_key,
    suggest_bilingual_name,
    suggest_bilingual_pair,
)
from .config import (
    BusinessConfig,
    DEFAULT_ACCENT_COLOR,
    DEFAULT_APP_LANGUAGE,
    DEFAULT_BACKUP_FREQUENCY,
    DEFAULT_BACKUP_RETENTION,
    DEFAULT_BUSINESS_COUNTRY,
    DEFAULT_BUSINESS_NAME,
    DEFAULT_CURRENCY,
    DEFAULT_FONT_SCALE_PCT,
    DEFAULT_IMAGE_PROVIDER,
    DEFAULT_RECEIPT_LANGUAGE,
    validate_app_language,
    validate_auto_lock_minutes,
    validate_backup_frequency,
    validate_country_code,
    validate_currency,
    validate_default_order_type_cfg,
    validate_discount_pct_cap,
    validate_font_scale_pct,
    validate_hex_color,
    validate_image_provider,
    validate_low_stock_alert_threshold,
    validate_receipt_language,
    validate_retention_count,
    validate_thermal_width_mm,
    validate_ui_density,
    validate_window_dimension,
    validate_window_state_pref,
)
from .i18n import get_language, normalize_language, tr, tr_role
from .payments import (
    ALL_PAYMENT_METHODS,
    METHOD_APPLE_PAY,
    METHOD_CARD,
    METHOD_CASH,
    METHOD_CUSTOM,
    METHOD_EWALLET,
    METHOD_INSTAPAY,
    STATUS_CASH_SETTLED,
    VERIFICATION_CASH_DRAWER,
    ValidatedPaymentPayload,
    parse_payment_settings,
    serialize_payment_settings,
    validate_payment_payload,
)

CURRENT_SCHEMA_VERSION = 8

ORDER_TYPE_DINE_IN = "DINE_IN"
ORDER_TYPE_TAKEAWAY = "TAKEAWAY"
ORDER_TYPE_DELIVERY = "DELIVERY"
VALID_ORDER_TYPES = {ORDER_TYPE_DINE_IN, ORDER_TYPE_TAKEAWAY, ORDER_TYPE_DELIVERY}

HELD_STATUS_HELD = "HELD"
HELD_STATUS_COMPLETED = "COMPLETED"
HELD_STATUS_CANCELLED = "CANCELLED"

MODIFIER_MODE_SINGLE = "single"
MODIFIER_MODE_MULTI = "multi"

VALID_INGREDIENT_UNITS = ("g", "kg", "ml", "l", "pcs")
QTY_MILLI_SCALE = 1000
DISCOUNT_TYPE_NONE = ""
DISCOUNT_TYPE_FIXED = "FIXED"
DISCOUNT_TYPE_PERCENT = "PERCENT"
VALID_DISCOUNT_TYPES = {DISCOUNT_TYPE_NONE, DISCOUNT_TYPE_FIXED, DISCOUNT_TYPE_PERCENT}


def validate_order_type(order_type: str | None) -> str:
    norm = (order_type or ORDER_TYPE_DINE_IN).strip().upper()
    if norm not in VALID_ORDER_TYPES:
        raise ValueError(tr("backend.err.order_type_invalid", order_type=order_type or ""))
    return norm


@dataclass(frozen=True)
class CategoryRecord:
    id: int
    normalized_key: str
    name_en: str
    name_ar: str
    display_order: int = 0
    is_system: bool = False
    is_archived: bool = False
    created_at: str = ""
    updated_at: str = ""

    @property
    def primary_name(self) -> str:
        return (self.name_en or self.name_ar).strip()

    def display_name(self, lang: str | None = None) -> str:
        resolved = get_language() if lang is None else normalize_language(lang)
        if resolved == "ar-EG":
            return (self.name_ar or self.name_en).strip()
        return (self.name_en or self.name_ar).strip()


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    category: str
    price_minor: int
    created_at: str
    updated_at: str
    image_filename: str = ""
    image_source: str = ""
    image_attribution: str = ""
    image_license: str = ""
    image_source_url: str = ""
    name_en: str = ""
    name_ar: str = ""
    category_id: int | None = None
    sku: str = ""

    def display_name(self, lang: str | None = None) -> str:
        resolved = get_language() if lang is None else normalize_language(lang)
        if resolved == "ar-EG":
            return (self.name_ar or self.name or self.name_en).strip()
        return (self.name_en or self.name or self.name_ar).strip()


@dataclass(frozen=True)
class ModifierOption:
    id: int
    group_id: int
    name_en: str
    name_ar: str = ""
    price_delta_minor: int = 0
    is_active: bool = True
    is_archived: bool = False
    display_order: int = 0
    effective_price_delta_minor: int | None = None
    has_price_override: bool = False

    def display_name(self, lang: str | None = None) -> str:
        resolved = get_language() if lang is None else normalize_language(lang)
        if resolved == "ar-EG":
            return (self.name_ar or self.name_en).strip()
        return (self.name_en or self.name_ar).strip()

    @property
    def resolved_price_delta_minor(self) -> int:
        if self.effective_price_delta_minor is not None:
            return int(self.effective_price_delta_minor)
        return int(self.price_delta_minor)


@dataclass(frozen=True)
class ModifierGroup:
    id: int
    name_en: str
    name_ar: str = ""
    selection_mode: str = MODIFIER_MODE_SINGLE
    is_required: bool = False
    min_select: int = 0
    max_select: int = 1
    is_active: bool = True
    is_archived: bool = False
    display_order: int = 0
    options: tuple[ModifierOption, ...] = ()
    assigned_product_ids: tuple[int, ...] = ()

    def display_name(self, lang: str | None = None) -> str:
        resolved = get_language() if lang is None else normalize_language(lang)
        if resolved == "ar-EG":
            return (self.name_ar or self.name_en).strip()
        return (self.name_en or self.name_ar).strip()

    @property
    def active_options(self) -> tuple[ModifierOption, ...]:
        return tuple(o for o in self.options if o.is_active and not o.is_archived)


@dataclass(frozen=True)
class CartModifierSelection:
    group_id: int | None
    option_id: int | None
    group_name: str
    group_name_en: str = ""
    group_name_ar: str = ""
    option_name: str = ""
    option_name_en: str = ""
    option_name_ar: str = ""
    price_delta_minor: int = 0

    def display_group_name(self, lang: str | None = None) -> str:
        resolved = get_language() if lang is None else normalize_language(lang)
        if resolved == "ar-EG":
            return (self.group_name_ar or self.group_name or self.group_name_en).strip()
        return (self.group_name_en or self.group_name or self.group_name_ar).strip()

    def display_option_name(self, lang: str | None = None) -> str:
        resolved = get_language() if lang is None else normalize_language(lang)
        if resolved == "ar-EG":
            return (self.option_name_ar or self.option_name or self.option_name_en).strip()
        return (self.option_name_en or self.option_name or self.option_name_ar).strip()


@dataclass(frozen=True)
class CartLine:
    product_id: int
    name: str
    unit_price_minor: int
    quantity: int
    image_filename: str = ""
    category: str = ""
    name_en: str = ""
    name_ar: str = ""
    modifiers: tuple[CartModifierSelection, ...] = ()
    base_unit_price_minor: int | None = None

    @property
    def modifiers_total_minor(self) -> int:
        return sum(max(0, int(m.price_delta_minor)) for m in self.modifiers)

    @property
    def resolved_base_unit_price_minor(self) -> int:
        if self.base_unit_price_minor is not None:
            return int(self.base_unit_price_minor)
        return max(0, int(self.unit_price_minor) - self.modifiers_total_minor)

    @property
    def cart_key(self) -> int | str:
        if not self.modifiers:
            return int(self.product_id)
        parts = sorted(
            (
                int(m.option_id) if m.option_id is not None else -1,
                int(m.group_id) if m.group_id is not None else -1,
                (m.option_name_en or m.option_name or "").strip(),
                int(m.price_delta_minor),
            )
            for m in self.modifiers
        )
        sig = "|".join(f"{oid}:{gid}:{oname}:{delta}" for oid, gid, oname, delta in parts)
        return f"{int(self.product_id)}:{sig}"

    @property
    def line_total_minor(self) -> int:
        return int(self.unit_price_minor) * int(self.quantity)

    def display_name(self, lang: str | None = None) -> str:
        resolved = get_language() if lang is None else normalize_language(lang)
        if resolved == "ar-EG":
            return (self.name_ar or self.name or self.name_en).strip()
        return (self.name_en or self.name or self.name_ar).strip()

    def modifiers_summary(self, lang: str | None = None, currency: str = "") -> str:
        if not self.modifiers:
            return ""
        parts: list[str] = []
        for m in self.modifiers:
            lbl = m.display_option_name(lang)
            if m.price_delta_minor > 0 and currency:
                from .money import format_minor

                lbl = f"{lbl} (+{format_minor(m.price_delta_minor, currency, lang=lang)})"
            parts.append(lbl)
        return " · ".join(parts)


@dataclass(frozen=True)
class OrderItemModifierRecord:
    id: int = 0
    order_item_id: int = 0
    modifier_group_id: int | None = None
    modifier_option_id: int | None = None
    group_name_snapshot: str = ""
    group_name_en: str = ""
    group_name_ar: str = ""
    option_name_snapshot: str = ""
    option_name_en: str = ""
    option_name_ar: str = ""
    price_delta_minor: int = 0

    def display_group_name(self, lang: str | None = None) -> str:
        resolved = get_language() if lang is None else normalize_language(lang)
        if resolved == "ar-EG":
            return (self.group_name_ar or self.group_name_snapshot or self.group_name_en).strip()
        return (self.group_name_en or self.group_name_snapshot or self.group_name_ar).strip()

    def display_option_name(self, lang: str | None = None) -> str:
        resolved = get_language() if lang is None else normalize_language(lang)
        if resolved == "ar-EG":
            return (self.option_name_ar or self.option_name_snapshot or self.option_name_en).strip()
        return (self.option_name_en or self.option_name_snapshot or self.option_name_ar).strip()


@dataclass(frozen=True)
class PaymentRecord:
    id: int
    order_id: int
    payment_method: str
    payment_provider: str = ""
    custom_method_label: str = ""
    payment_amount_minor: int = 0
    currency: str = "USD"
    payment_status: str = STATUS_CASH_SETTLED
    external_reference: str = ""
    confirmed_by_user_id: int | None = None
    confirmed_by_name: str = ""
    confirmed_at: str = ""
    verification_source: str = VERIFICATION_CASH_DRAWER


@dataclass(frozen=True)
class OrderItemRecord:
    product_id: int | None
    product_name: str
    unit_price_minor: int
    quantity: int
    line_total_minor: int
    product_name_en: str = ""
    product_name_ar: str = ""
    category_snapshot: str = ""
    base_unit_price_minor: int = 0
    modifiers: tuple[OrderItemModifierRecord, ...] = ()
    id: int = 0
    discount_minor: int = 0
    discount_type: str = ""
    discount_value: int = 0
    estimated_cost_minor: int = 0
    has_cost_data: bool = False

    def display_name(self, lang: str | None = None) -> str:
        resolved = get_language() if lang is None else normalize_language(lang)
        if resolved == "ar-EG":
            return (self.product_name_ar or self.product_name or self.product_name_en).strip()
        return (self.product_name_en or self.product_name or self.product_name_ar).strip()


@dataclass(frozen=True)
class OrderRefundItemRecord:
    id: int
    refund_id: int
    order_item_id: int
    product_id: int | None
    product_name: str
    quantity: int
    refund_amount_minor: int
    stock_action: str = "NONE"


@dataclass(frozen=True)
class OrderRefundRecord:
    id: int
    refund_number: str
    order_id: int
    shift_id: int | None
    refund_type: str
    refund_method: str
    refund_amount_minor: int
    currency: str
    reason: str
    refund_status: str
    external_reference: str
    processed_by_user_id: int | None
    processed_by_name: str
    created_at: str
    items: tuple[OrderRefundItemRecord, ...] = ()


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
    cashier_user_id: int | None = None
    cashier_name_snapshot: str = ""
    receipt_number: str = ""
    tax_minor: int = 0
    discount_minor: int = 0
    payments: tuple[PaymentRecord, ...] | list[PaymentRecord] = ()
    order_type: str = ORDER_TYPE_DINE_IN
    table_number: str = ""
    customer_name: str = ""
    customer_phone: str = ""
    delivery_address: str = ""
    order_notes: str = ""
    shift_id: int | None = None
    discount_type: str = ""
    discount_value: int = 0
    discount_label: str = ""
    refunded_total_minor: int = 0
    refunds: tuple[OrderRefundRecord, ...] = ()

    @property
    def primary_payment(self) -> PaymentRecord | None:
        return self.payments[0] if self.payments else None

    @property
    def is_split_payment(self) -> bool:
        return len(self.payments) > 1

    @property
    def net_paid_minor(self) -> int:
        return max(0, int(self.total_minor) - int(self.refunded_total_minor))


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
    cashier_user_id: int | None = None
    cashier_name_snapshot: str = ""
    receipt_number: str = ""
    payment_method: str = METHOD_CASH
    payment_provider: str = ""
    custom_method_label: str = ""
    payment_status: str = STATUS_CASH_SETTLED
    external_reference: str = ""
    order_type: str = ORDER_TYPE_DINE_IN
    table_number: str = ""
    customer_name: str = ""
    discount_minor: int = 0
    refunded_total_minor: int = 0
    payment_count: int = 1
    shift_id: int | None = None


@dataclass(frozen=True)
class HeldOrderItemRecord:
    id: int
    held_order_id: int
    product_id: int | None
    product_name: str
    product_name_en: str
    product_name_ar: str
    category_snapshot: str
    image_filename: str
    base_unit_price_minor: int
    unit_price_minor: int
    quantity: int
    modifiers: tuple[CartModifierSelection, ...] = ()

    def display_name(self, lang: str | None = None) -> str:
        resolved = get_language() if lang is None else normalize_language(lang)
        if resolved == "ar-EG":
            return (self.product_name_ar or self.product_name or self.product_name_en).strip()
        return (self.product_name_en or self.product_name or self.product_name_ar).strip()

    def to_cart_line(self) -> CartLine:
        return CartLine(
            product_id=int(self.product_id or 0),
            name=self.product_name,
            unit_price_minor=int(self.unit_price_minor),
            quantity=int(self.quantity),
            image_filename=self.image_filename,
            category=self.category_snapshot,
            name_en=self.product_name_en,
            name_ar=self.product_name_ar,
            modifiers=tuple(self.modifiers),
            base_unit_price_minor=int(self.base_unit_price_minor),
        )


@dataclass(frozen=True)
class HeldOrderRecord:
    id: int
    hold_number: str
    status: str
    order_type: str
    table_number: str
    customer_name: str
    customer_phone: str
    delivery_address: str
    order_notes: str
    currency: str
    subtotal_minor: int
    item_count: int
    cashier_user_id: int | None
    cashier_name_snapshot: str
    created_at: str
    updated_at: str
    completed_order_id: int | None = None
    items: tuple[HeldOrderItemRecord, ...] = ()


@dataclass(frozen=True)
class DailySalesPoint:
    date_key: str
    label: str
    total_minor: int
    order_count: int


@dataclass(frozen=True)
class CategorySalesPoint:
    category: str
    total_minor: int
    units_sold: int


@dataclass(frozen=True)
class ProductSalesPoint:
    product_name: str
    units_sold: int
    total_minor: int


@dataclass(frozen=True)
class SalesAnalyticsSummary:
    period: str
    order_count: int
    total_revenue_minor: int
    total_items_sold: int
    average_order_minor: int
    today_revenue_minor: int
    today_order_count: int
    daily_points: list[DailySalesPoint]
    category_sales: list[CategorySalesPoint]
    top_products: list[ProductSalesPoint]
    category_daily_series: dict[str, list[int]]


DEFAULT_SYSTEM_CATEGORIES: list[tuple[str, str, int, int]] = [
    ("Espresso", "إسبريسو", 10, 1),
    ("Cold Drinks", "مشروبات باردة", 20, 1),
    ("Tea & Matcha", "شاي وماتشا", 30, 1),
    ("Bakery", "مخبوزات", 40, 1),
    ("Kitchen", "المطبخ", 50, 1),
    ("Retail", "منتجات البيع", 60, 1),
    ("Other", "أخرى", 990, 1),
]

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

DEFAULT_PRODUCT_AR_NAMES: dict[str, str] = {
    "Double Espresso": "دبل إسبريسو",
    "Flat White": "فلات وايت",
    "Classic Cappuccino": "كابتشينو كلاسيك",
    "Caramel Latte": "لاتيه بالكراميل",
    "Iced Americano": "أمريكانو مثلج",
    "Iced Vanilla Latte": "لاتيه فانيليا مثلج",
    "Ceremonial Matcha": "ماتشا احتفالية",
    "Spiced Chai Latte": "شاي لاتيه بالتوابل",
    "Butter Croissant": "كرواسون بالزبدة",
    "Chocolate Muffin": "مافن الشوكولاتة",
    "Turkey Pesto Panini": "بانيني ديك رومي بالبيستو",
    "House Blend Beans (250g)": "حبوب القهوة الخاصة (250 جم)",
}

REQUIRED_TABLES = {"app_meta", "business_settings", "products", "orders", "order_items"}


def _utc_now_iso(now: datetime | None = None) -> str:
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_iso_to_utc(iso_str: str) -> datetime | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _parse_iso_to_local(iso_str: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except ValueError:
        return None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name});").fetchall()
    return {row["name"] for row in rows}


def _row_to_category(r: sqlite3.Row) -> CategoryRecord:
    keys = r.keys()
    return CategoryRecord(
        id=int(r["id"]),
        normalized_key=r["normalized_key"],
        name_en=(r["name_en"] or "").strip(),
        name_ar=(r["name_ar"] or "").strip(),
        display_order=int(r["display_order"]) if "display_order" in keys else 0,
        is_system=bool(r["is_system"]) if "is_system" in keys else False,
        is_archived=bool(r["is_archived"]) if "is_archived" in keys else False,
        created_at=(r["created_at"] if "created_at" in keys else "") or "",
        updated_at=(r["updated_at"] if "updated_at" in keys else "") or "",
    )


def _row_to_payment(r: sqlite3.Row) -> PaymentRecord:
    keys = r.keys()
    return PaymentRecord(
        id=int(r["id"]),
        order_id=int(r["order_id"]),
        payment_method=r["payment_method"],
        payment_provider=(r["payment_provider"] if "payment_provider" in keys else "") or "",
        custom_method_label=(
            (r["custom_method_label"] if "custom_method_label" in keys else "") or ""
        ),
        payment_amount_minor=int(r["payment_amount_minor"]),
        currency=r["currency"],
        payment_status=r["payment_status"],
        external_reference=(
            (r["external_reference"] if "external_reference" in keys else "") or ""
        ),
        confirmed_by_user_id=(
            int(r["confirmed_by_user_id"])
            if "confirmed_by_user_id" in keys and r["confirmed_by_user_id"] is not None
            else None
        ),
        confirmed_by_name=(
            (r["confirmed_by_name"] if "confirmed_by_name" in keys else "") or ""
        ),
        confirmed_at=(r["confirmed_at"] if "confirmed_at" in keys else "") or "",
        verification_source=(
            (r["verification_source"] if "verification_source" in keys else "")
            or VERIFICATION_CASH_DRAWER
        ),
    )


def _row_to_product(r: sqlite3.Row) -> Product:
    keys = r.keys()
    return Product(
        id=int(r["id"]),
        name=r["name"],
        category=r["category"],
        price_minor=int(r["price_minor"]),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        image_filename=(r["image_filename"] if "image_filename" in keys else "") or "",
        image_source=(r["image_source"] if "image_source" in keys else "") or "",
        image_attribution=(r["image_attribution"] if "image_attribution" in keys else "") or "",
        image_license=(r["image_license"] if "image_license" in keys else "") or "",
        image_source_url=(r["image_source_url"] if "image_source_url" in keys else "") or "",
        name_en=(r["name_en"] if "name_en" in keys else "") or "",
        name_ar=(r["name_ar"] if "name_ar" in keys else "") or "",
        category_id=(
            int(r["category_id"])
            if "category_id" in keys and r["category_id"] is not None
            else None
        ),
        sku=(r["sku"] if "sku" in keys else "") or "",
    )


def _row_to_user(r: sqlite3.Row) -> UserAccount:
    keys = r.keys()
    raw_rm = r["reduced_motion_pref"] if "reduced_motion_pref" in keys else -1
    try:
        rm_pref = int(raw_rm)
    except (TypeError, ValueError):
        rm_pref = -1
    if rm_pref not in (-1, 0, 1):
        rm_pref = -1
    return UserAccount(
        id=int(r["id"]),
        username=r["username"],
        display_name=r["display_name"] or r["username"],
        role=r["role"],
        is_active=bool(r["is_active"]),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        last_login_at=r["last_login_at"] or "",
        preferred_language=validate_preferred_language(
            r["preferred_language"] if "preferred_language" in keys else ""
        ),
        font_scale_pct=validate_font_scale_pct(
            r["font_scale_pct"] if "font_scale_pct" in keys else DEFAULT_FONT_SCALE_PCT
        ),
        ui_density=validate_ui_density(
            r["ui_density"] if "ui_density" in keys else "comfortable"
        ),
        reduced_motion_pref=rm_pref,
    )


def _row_to_settings(row: sqlite3.Row | None) -> BusinessConfig:
    if row is None:
        return BusinessConfig()
    keys = row.keys()
    return BusinessConfig(
        business_name=row["business_name"],
        accent_color=validate_hex_color(row["accent_color"]),
        currency=validate_currency(row["currency"]),
        address=row["address"] or "",
        phone=row["phone"] or "",
        logo_blob=bytes(row["logo_blob"] or b""),
        logo_ext=row["logo_ext"] or "",
        setup_completed=bool(row["setup_completed"]),
        image_provider=validate_image_provider(
            row["image_provider"] if "image_provider" in keys else DEFAULT_IMAGE_PROVIDER
        ),
        pexels_api_key=(row["pexels_api_key"] if "pexels_api_key" in keys else "") or "",
        reduced_motion=bool(row["reduced_motion"]) if "reduced_motion" in keys else False,
        auto_lock_minutes=validate_auto_lock_minutes(
            row["auto_lock_minutes"] if "auto_lock_minutes" in keys else 0
        ),
        backup_enabled=bool(row["backup_enabled"]) if "backup_enabled" in keys else False,
        backup_frequency=validate_backup_frequency(
            row["backup_frequency"] if "backup_frequency" in keys else DEFAULT_BACKUP_FREQUENCY
        ),
        backup_destination=(
            (row["backup_destination"] if "backup_destination" in keys else "") or ""
        ),
        backup_retention_count=validate_retention_count(
            row["backup_retention_count"]
            if "backup_retention_count" in keys
            else DEFAULT_BACKUP_RETENTION
        ),
        last_auto_backup_at=(
            (row["last_auto_backup_at"] if "last_auto_backup_at" in keys else "") or ""
        ),
        app_language=validate_app_language(
            row["app_language"] if "app_language" in keys else DEFAULT_APP_LANGUAGE
        ),
        receipt_language=validate_receipt_language(
            row["receipt_language"] if "receipt_language" in keys else DEFAULT_RECEIPT_LANGUAGE
        ),
        business_country=validate_country_code(
            row["business_country"] if "business_country" in keys else DEFAULT_BUSINESS_COUNTRY
        ),
        payment_methods_json=(
            (row["payment_methods_json"] if "payment_methods_json" in keys else "") or ""
        ),
        auto_save_receipt_pdf=(
            bool(row["auto_save_receipt_pdf"])
            if "auto_save_receipt_pdf" in keys
            else False
        ),
        receipt_export_dir=(
            (row["receipt_export_dir"] if "receipt_export_dir" in keys else "") or ""
        ),
        online_translation_enabled=(
            bool(row["online_translation_enabled"])
            if "online_translation_enabled" in keys
            else False
        ),
        country_configured=(
            bool(row["country_configured"])
            if "country_configured" in keys
            else False
        ),
        window_state_pref=validate_window_state_pref(
            row["window_state_pref"] if "window_state_pref" in keys else "maximized"
        ),
        window_width=validate_window_dimension(
            row["window_width"] if "window_width" in keys else 1280,
            default=1280,
            min_val=900,
            max_val=4096,
        ),
        window_height=validate_window_dimension(
            row["window_height"] if "window_height" in keys else 800,
            default=800,
            min_val=485,
            max_val=2560,
        ),
        default_font_scale_pct=validate_font_scale_pct(
            row["default_font_scale_pct"] if "default_font_scale_pct" in keys else DEFAULT_FONT_SCALE_PCT
        ),
        default_ui_density=validate_ui_density(
            row["default_ui_density"] if "default_ui_density" in keys else "comfortable"
        ),
        allow_cashier_discounts=(
            bool(row["allow_cashier_discounts"])
            if "allow_cashier_discounts" in keys
            else True
        ),
        allow_cashier_refunds=(
            bool(row["allow_cashier_refunds"])
            if "allow_cashier_refunds" in keys
            else False
        ),
        max_cashier_discount_pct=validate_discount_pct_cap(
            row["max_cashier_discount_pct"] if "max_cashier_discount_pct" in keys else 15
        ),
        default_order_type=validate_default_order_type_cfg(
            row["default_order_type"] if "default_order_type" in keys else "DINE_IN"
        ),
        enable_dine_in=bool(row["enable_dine_in"]) if "enable_dine_in" in keys else True,
        enable_takeaway=bool(row["enable_takeaway"]) if "enable_takeaway" in keys else True,
        enable_delivery=bool(row["enable_delivery"]) if "enable_delivery" in keys else True,
        modifiers_enabled=bool(row["modifiers_enabled"]) if "modifiers_enabled" in keys else True,
        inventory_tracking_enabled=(
            bool(row["inventory_tracking_enabled"])
            if "inventory_tracking_enabled" in keys
            else True
        ),
        auto_deduct_inventory=(
            bool(row["auto_deduct_inventory"])
            if "auto_deduct_inventory" in keys
            else True
        ),
        default_low_stock_alert=validate_low_stock_alert_threshold(
            row["default_low_stock_alert"] if "default_low_stock_alert" in keys else 10
        ),
        require_shift_for_sales=(
            bool(row["require_shift_for_sales"])
            if "require_shift_for_sales" in keys
            else False
        ),
        require_opening_cash=(
            bool(row["require_opening_cash"])
            if "require_opening_cash" in keys
            else True
        ),
        show_cashier_on_receipt=(
            bool(row["show_cashier_on_receipt"])
            if "show_cashier_on_receipt" in keys
            else True
        ),
        show_business_details_on_receipt=(
            bool(row["show_business_details_on_receipt"])
            if "show_business_details_on_receipt" in keys
            else True
        ),
        printer_name=(row["printer_name"] if "printer_name" in keys else "") or "",
        printer_paper_width_mm=validate_thermal_width_mm(
            row["printer_paper_width_mm"] if "printer_paper_width_mm" in keys else 80
        ),
        auto_print_receipt=(
            bool(row["auto_print_receipt"])
            if "auto_print_receipt" in keys
            else False
        ),
    )


class Database(DatabaseInventoryMixin, DatabaseFinanceMixin):
    """Local SQLite persistence for Café POS with non-destructive migrations and RBAC guards."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._session_actor: UserAccount | None = None
        # Bounded in-memory cooldown map for unknown usernames (prevents enumeration bypass)
        self._unknown_login_attempts: dict[str, tuple[int, str]] = {}
        self._init_schema()

    def set_session_actor(self, user: UserAccount | None) -> None:
        self._session_actor = user

    @property
    def session_actor(self) -> UserAccount | None:
        return self._session_actor

    @contextmanager
    def _tx(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = DELETE;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def close(self) -> None:
        """Ensure transient references are released before file-level backup/restore swaps."""
        gc.collect()

    def _create_premigration_backup(self, old_version: int) -> Path | None:
        """Create a consistent safety copy before running schema upgrades from v1..v4."""
        if not self.db_path.exists() or self.db_path.stat().st_size < 512:
            return None
        safety_dir = self.db_path.parent / "backups" / "safety"
        safety_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        candidate = safety_dir / f"CafePOS_PreMigration_v{old_version}_to_v{CURRENT_SCHEMA_VERSION}_{stamp}.db"
        idx = 2
        while candidate.exists():
            candidate = safety_dir / (
                f"CafePOS_PreMigration_v{old_version}_to_v{CURRENT_SCHEMA_VERSION}_{stamp}_{idx}.db"
            )
            idx += 1
        self.create_consistent_sqlite_snapshot(candidate)
        return candidate

    def _detect_existing_schema_version(self) -> int | None:
        if not self.db_path.exists() or self.db_path.stat().st_size < 512:
            return None
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table';"
                ).fetchall()
            }
            if "business_settings" not in tables:
                return None
            if "app_meta" in tables:
                row = conn.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version';"
                ).fetchone()
                if row and str(row[0]).isdigit():
                    return int(row[0])
            return 1
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def _init_schema(self) -> None:
        prior_ver = self._detect_existing_schema_version()
        if prior_ver is not None and prior_ver < CURRENT_SCHEMA_VERSION:
            self._create_premigration_backup(prior_ver)

        with self._tx() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL CHECK (role IN ('ADMIN', 'CASHIER')),
                    password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL DEFAULT '',
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT NOT NULL DEFAULT '',
                    preferred_language TEXT NOT NULL DEFAULT ''
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
                    receipt_language TEXT NOT NULL DEFAULT 'follow',
                    business_country TEXT NOT NULL DEFAULT 'US',
                    payment_methods_json TEXT NOT NULL DEFAULT '',
                    auto_save_receipt_pdf INTEGER NOT NULL DEFAULT 0,
                    receipt_export_dir TEXT NOT NULL DEFAULT '',
                    online_translation_enabled INTEGER NOT NULL DEFAULT 0,
                    country_configured INTEGER NOT NULL DEFAULT 0,
                    next_hold_seq INTEGER NOT NULL DEFAULT 101
                );

                CREATE TABLE IF NOT EXISTS categories (
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

                CREATE TABLE IF NOT EXISTS translation_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_text TEXT NOT NULL,
                    source_lang TEXT NOT NULL CHECK (source_lang IN ('en', 'ar')),
                    target_text TEXT NOT NULL,
                    target_lang TEXT NOT NULL CHECK (target_lang IN ('en', 'ar')),
                    confidence TEXT NOT NULL DEFAULT 'medium',
                    source TEXT NOT NULL DEFAULT 'offline_dict',
                    is_user_confirmed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_text, source_lang, target_lang)
                );

                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    price_minor INTEGER NOT NULL CHECK (price_minor >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    image_filename TEXT NOT NULL DEFAULT '',
                    image_source TEXT NOT NULL DEFAULT '',
                    image_attribution TEXT NOT NULL DEFAULT '',
                    image_license TEXT NOT NULL DEFAULT '',
                    image_source_url TEXT NOT NULL DEFAULT '',
                    name_en TEXT NOT NULL DEFAULT '',
                    name_ar TEXT NOT NULL DEFAULT '',
                    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                    sku TEXT NOT NULL DEFAULT ''
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
                    business_phone TEXT NOT NULL DEFAULT '',
                    cashier_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    cashier_name_snapshot TEXT NOT NULL DEFAULT '',
                    receipt_number TEXT NOT NULL DEFAULT '',
                    tax_minor INTEGER NOT NULL DEFAULT 0,
                    discount_minor INTEGER NOT NULL DEFAULT 0,
                    client_tx_key TEXT NOT NULL DEFAULT '',
                    business_country_snapshot TEXT NOT NULL DEFAULT '',
                    order_type TEXT NOT NULL DEFAULT 'DINE_IN',
                    table_number TEXT NOT NULL DEFAULT '',
                    customer_name TEXT NOT NULL DEFAULT '',
                    customer_phone TEXT NOT NULL DEFAULT '',
                    delivery_address TEXT NOT NULL DEFAULT '',
                    order_notes TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    product_id INTEGER,
                    product_name TEXT NOT NULL,
                    unit_price_minor INTEGER NOT NULL CHECK (unit_price_minor >= 0),
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    line_total_minor INTEGER NOT NULL CHECK (line_total_minor >= 0),
                    product_name_en TEXT NOT NULL DEFAULT '',
                    product_name_ar TEXT NOT NULL DEFAULT '',
                    category_snapshot TEXT NOT NULL DEFAULT '',
                    base_unit_price_minor INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS modifier_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name_en TEXT NOT NULL DEFAULT '',
                    name_ar TEXT NOT NULL DEFAULT '',
                    selection_mode TEXT NOT NULL DEFAULT 'single' CHECK (selection_mode IN ('single', 'multi')),
                    is_required INTEGER NOT NULL DEFAULT 0,
                    min_select INTEGER NOT NULL DEFAULT 0 CHECK (min_select >= 0),
                    max_select INTEGER NOT NULL DEFAULT 1 CHECK (max_select >= 0),
                    is_active INTEGER NOT NULL DEFAULT 1,
                    is_archived INTEGER NOT NULL DEFAULT 0,
                    display_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS modifier_options (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL REFERENCES modifier_groups(id) ON DELETE CASCADE,
                    name_en TEXT NOT NULL DEFAULT '',
                    name_ar TEXT NOT NULL DEFAULT '',
                    price_delta_minor INTEGER NOT NULL DEFAULT 0 CHECK (price_delta_minor >= 0),
                    is_active INTEGER NOT NULL DEFAULT 1,
                    is_archived INTEGER NOT NULL DEFAULT 0,
                    display_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS product_modifier_groups (
                    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    group_id INTEGER NOT NULL REFERENCES modifier_groups(id) ON DELETE CASCADE,
                    display_order INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (product_id, group_id)
                );

                CREATE TABLE IF NOT EXISTS product_modifier_option_overrides (
                    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    option_id INTEGER NOT NULL REFERENCES modifier_options(id) ON DELETE CASCADE,
                    price_delta_minor INTEGER NOT NULL CHECK (price_delta_minor >= 0),
                    PRIMARY KEY (product_id, option_id)
                );

                CREATE TABLE IF NOT EXISTS order_item_modifiers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_item_id INTEGER NOT NULL REFERENCES order_items(id) ON DELETE CASCADE,
                    modifier_group_id INTEGER,
                    modifier_option_id INTEGER,
                    group_name_snapshot TEXT NOT NULL DEFAULT '',
                    group_name_en TEXT NOT NULL DEFAULT '',
                    group_name_ar TEXT NOT NULL DEFAULT '',
                    option_name_snapshot TEXT NOT NULL DEFAULT '',
                    option_name_en TEXT NOT NULL DEFAULT '',
                    option_name_ar TEXT NOT NULL DEFAULT '',
                    price_delta_minor INTEGER NOT NULL DEFAULT 0 CHECK (price_delta_minor >= 0)
                );

                CREATE TABLE IF NOT EXISTS held_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hold_number TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'HELD' CHECK (status IN ('HELD', 'COMPLETED', 'CANCELLED')),
                    order_type TEXT NOT NULL DEFAULT 'DINE_IN' CHECK (order_type IN ('DINE_IN', 'TAKEAWAY', 'DELIVERY')),
                    table_number TEXT NOT NULL DEFAULT '',
                    customer_name TEXT NOT NULL DEFAULT '',
                    customer_phone TEXT NOT NULL DEFAULT '',
                    delivery_address TEXT NOT NULL DEFAULT '',
                    order_notes TEXT NOT NULL DEFAULT '',
                    currency TEXT NOT NULL,
                    subtotal_minor INTEGER NOT NULL CHECK (subtotal_minor >= 0),
                    item_count INTEGER NOT NULL CHECK (item_count >= 0),
                    cashier_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    cashier_name_snapshot TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_order_id INTEGER REFERENCES orders(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS held_order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    held_order_id INTEGER NOT NULL REFERENCES held_orders(id) ON DELETE CASCADE,
                    product_id INTEGER,
                    product_name TEXT NOT NULL,
                    product_name_en TEXT NOT NULL DEFAULT '',
                    product_name_ar TEXT NOT NULL DEFAULT '',
                    category_snapshot TEXT NOT NULL DEFAULT '',
                    image_filename TEXT NOT NULL DEFAULT '',
                    base_unit_price_minor INTEGER NOT NULL DEFAULT 0 CHECK (base_unit_price_minor >= 0),
                    unit_price_minor INTEGER NOT NULL CHECK (unit_price_minor >= 0),
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    modifiers_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    payment_method TEXT NOT NULL CHECK (
                        payment_method IN ('CASH', 'APPLE_PAY', 'INSTAPAY', 'EWALLET', 'CARD', 'CUSTOM')
                    ),
                    payment_provider TEXT NOT NULL DEFAULT '',
                    custom_method_label TEXT NOT NULL DEFAULT '',
                    payment_amount_minor INTEGER NOT NULL CHECK (payment_amount_minor >= 0),
                    currency TEXT NOT NULL,
                    payment_status TEXT NOT NULL CHECK (
                        payment_status IN ('CASH_SETTLED', 'CONFIRMED_EXTERNAL')
                    ),
                    external_reference TEXT NOT NULL DEFAULT '',
                    confirmed_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    confirmed_by_name TEXT NOT NULL DEFAULT '',
                    confirmed_at TEXT NOT NULL,
                    verification_source TEXT NOT NULL CHECK (
                        verification_source IN ('cashier_cash_drawer', 'cashier_manual_confirmation')
                    )
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
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
                """
            )

            # Non-destructive v1 -> v8 column migrations
            user_cols = _table_columns(conn, "users")
            user_migrations = [
                ("preferred_language", "TEXT NOT NULL DEFAULT ''"),
                ("font_scale_pct", "INTEGER NOT NULL DEFAULT 100"),
                ("ui_density", "TEXT NOT NULL DEFAULT 'comfortable'"),
                ("reduced_motion_pref", "INTEGER NOT NULL DEFAULT -1"),
            ]
            for col_name, col_def in user_migrations:
                if col_name not in user_cols:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def};")

            biz_cols = _table_columns(conn, "business_settings")
            biz_migrations = [
                ("image_provider", "TEXT NOT NULL DEFAULT 'openverse'"),
                ("pexels_api_key", "TEXT NOT NULL DEFAULT ''"),
                ("reduced_motion", "INTEGER NOT NULL DEFAULT 0"),
                ("auto_lock_minutes", "INTEGER NOT NULL DEFAULT 0"),
                ("backup_enabled", "INTEGER NOT NULL DEFAULT 0"),
                ("backup_frequency", "TEXT NOT NULL DEFAULT 'daily'"),
                ("backup_destination", "TEXT NOT NULL DEFAULT ''"),
                ("backup_retention_count", "INTEGER NOT NULL DEFAULT 7"),
                ("last_auto_backup_at", "TEXT NOT NULL DEFAULT ''"),
                ("app_language", "TEXT NOT NULL DEFAULT 'en'"),
                ("receipt_language", "TEXT NOT NULL DEFAULT 'follow'"),
                ("business_country", "TEXT NOT NULL DEFAULT 'US'"),
                ("payment_methods_json", "TEXT NOT NULL DEFAULT ''"),
                ("auto_save_receipt_pdf", "INTEGER NOT NULL DEFAULT 0"),
                ("receipt_export_dir", "TEXT NOT NULL DEFAULT ''"),
                ("online_translation_enabled", "INTEGER NOT NULL DEFAULT 0"),
                ("country_configured", "INTEGER NOT NULL DEFAULT 0"),
                ("next_hold_seq", "INTEGER NOT NULL DEFAULT 101"),
                ("window_state_pref", "TEXT NOT NULL DEFAULT 'maximized'"),
                ("window_width", "INTEGER NOT NULL DEFAULT 1280"),
                ("window_height", "INTEGER NOT NULL DEFAULT 800"),
                ("default_font_scale_pct", "INTEGER NOT NULL DEFAULT 100"),
                ("default_ui_density", "TEXT NOT NULL DEFAULT 'comfortable'"),
                ("allow_cashier_discounts", "INTEGER NOT NULL DEFAULT 1"),
                ("allow_cashier_refunds", "INTEGER NOT NULL DEFAULT 0"),
                ("max_cashier_discount_pct", "INTEGER NOT NULL DEFAULT 15"),
                ("default_order_type", "TEXT NOT NULL DEFAULT 'DINE_IN'"),
                ("enable_dine_in", "INTEGER NOT NULL DEFAULT 1"),
                ("enable_takeaway", "INTEGER NOT NULL DEFAULT 1"),
                ("enable_delivery", "INTEGER NOT NULL DEFAULT 1"),
                ("modifiers_enabled", "INTEGER NOT NULL DEFAULT 1"),
                ("inventory_tracking_enabled", "INTEGER NOT NULL DEFAULT 1"),
                ("auto_deduct_inventory", "INTEGER NOT NULL DEFAULT 1"),
                ("default_low_stock_alert", "INTEGER NOT NULL DEFAULT 10"),
                ("require_shift_for_sales", "INTEGER NOT NULL DEFAULT 0"),
                ("require_opening_cash", "INTEGER NOT NULL DEFAULT 1"),
                ("show_cashier_on_receipt", "INTEGER NOT NULL DEFAULT 1"),
                ("show_business_details_on_receipt", "INTEGER NOT NULL DEFAULT 1"),
                ("printer_name", "TEXT NOT NULL DEFAULT ''"),
                ("printer_paper_width_mm", "INTEGER NOT NULL DEFAULT 80"),
                ("auto_print_receipt", "INTEGER NOT NULL DEFAULT 0"),
                ("next_shift_seq", "INTEGER NOT NULL DEFAULT 101"),
                ("next_refund_seq", "INTEGER NOT NULL DEFAULT 1001"),
            ]
            for col_name, col_def in biz_migrations:
                if col_name not in biz_cols:
                    conn.execute(f"ALTER TABLE business_settings ADD COLUMN {col_name} {col_def};")

            # Non-destructive v5 -> v6: widen payments.payment_method CHECK to include 'APPLE_PAY'
            pay_ddl_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'payments';"
            ).fetchone()
            pay_ddl_sql = str(pay_ddl_row["sql"] or "") if pay_ddl_row else ""
            if pay_ddl_sql and "'APPLE_PAY'" not in pay_ddl_sql:
                conn.executescript(
                    """
                    CREATE TABLE payments_v6_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                        payment_method TEXT NOT NULL CHECK (
                            payment_method IN ('CASH', 'APPLE_PAY', 'INSTAPAY', 'EWALLET', 'CARD', 'CUSTOM')
                        ),
                        payment_provider TEXT NOT NULL DEFAULT '',
                        custom_method_label TEXT NOT NULL DEFAULT '',
                        payment_amount_minor INTEGER NOT NULL CHECK (payment_amount_minor >= 0),
                        currency TEXT NOT NULL,
                        payment_status TEXT NOT NULL CHECK (
                            payment_status IN ('CASH_SETTLED', 'CONFIRMED_EXTERNAL')
                        ),
                        external_reference TEXT NOT NULL DEFAULT '',
                        confirmed_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                        confirmed_by_name TEXT NOT NULL DEFAULT '',
                        confirmed_at TEXT NOT NULL,
                        verification_source TEXT NOT NULL CHECK (
                            verification_source IN ('cashier_cash_drawer', 'cashier_manual_confirmation')
                        )
                    );
                    INSERT INTO payments_v6_new (
                        id, order_id, payment_method, payment_provider, custom_method_label,
                        payment_amount_minor, currency, payment_status, external_reference,
                        confirmed_by_user_id, confirmed_by_name, confirmed_at, verification_source
                    )
                    SELECT
                        id, order_id, payment_method, payment_provider, custom_method_label,
                        payment_amount_minor, currency, payment_status, external_reference,
                        confirmed_by_user_id, confirmed_by_name, confirmed_at, verification_source
                    FROM payments;
                    DROP TABLE payments;
                    ALTER TABLE payments_v6_new RENAME TO payments;
                    CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id);
                    """
                )

            prod_cols = _table_columns(conn, "products")
            for col_name in (
                "image_filename",
                "image_source",
                "image_attribution",
                "image_license",
                "image_source_url",
                "name_en",
                "name_ar",
                "sku",
            ):
                if col_name not in prod_cols:
                    conn.execute(
                        f"ALTER TABLE products ADD COLUMN {col_name} TEXT NOT NULL DEFAULT '';"
                    )
            if "category_id" not in prod_cols:
                conn.execute(
                    "ALTER TABLE products ADD COLUMN category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL;"
                )

            order_cols = _table_columns(conn, "orders")
            if "cashier_user_id" not in order_cols:
                conn.execute(
                    "ALTER TABLE orders ADD COLUMN cashier_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;"
                )
            if "cashier_name_snapshot" not in order_cols:
                conn.execute(
                    "ALTER TABLE orders ADD COLUMN cashier_name_snapshot TEXT NOT NULL DEFAULT '';"
                )
            for col_name, col_def in (
                ("receipt_number", "TEXT NOT NULL DEFAULT ''"),
                ("tax_minor", "INTEGER NOT NULL DEFAULT 0"),
                ("discount_minor", "INTEGER NOT NULL DEFAULT 0"),
                ("client_tx_key", "TEXT NOT NULL DEFAULT ''"),
                ("business_country_snapshot", "TEXT NOT NULL DEFAULT ''"),
                ("order_type", "TEXT NOT NULL DEFAULT 'DINE_IN'"),
                ("table_number", "TEXT NOT NULL DEFAULT ''"),
                ("customer_name", "TEXT NOT NULL DEFAULT ''"),
                ("customer_phone", "TEXT NOT NULL DEFAULT ''"),
                ("delivery_address", "TEXT NOT NULL DEFAULT ''"),
                ("order_notes", "TEXT NOT NULL DEFAULT ''"),
                ("shift_id", "INTEGER"),
                ("discount_type", "TEXT NOT NULL DEFAULT ''"),
                ("discount_value", "INTEGER NOT NULL DEFAULT 0"),
                ("discount_label", "TEXT NOT NULL DEFAULT ''"),
                ("refunded_total_minor", "INTEGER NOT NULL DEFAULT 0"),
                ("estimated_cost_minor", "INTEGER NOT NULL DEFAULT 0"),
                ("has_cost_data", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if col_name not in order_cols:
                    conn.execute(f"ALTER TABLE orders ADD COLUMN {col_name} {col_def};")

            item_cols = _table_columns(conn, "order_items")
            for col_name in ("product_name_en", "product_name_ar", "category_snapshot"):
                if col_name not in item_cols:
                    conn.execute(
                        f"ALTER TABLE order_items ADD COLUMN {col_name} TEXT NOT NULL DEFAULT '';"
                    )
            for col_name, col_def in (
                ("base_unit_price_minor", "INTEGER NOT NULL DEFAULT 0"),
                ("discount_minor", "INTEGER NOT NULL DEFAULT 0"),
                ("discount_type", "TEXT NOT NULL DEFAULT ''"),
                ("discount_value", "INTEGER NOT NULL DEFAULT 0"),
                ("estimated_unit_cost_minor", "INTEGER NOT NULL DEFAULT 0"),
                ("estimated_cost_minor", "INTEGER NOT NULL DEFAULT 0"),
                ("has_cost_data", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if col_name not in item_cols:
                    conn.execute(f"ALTER TABLE order_items ADD COLUMN {col_name} {col_def};")

            held_cols = _table_columns(conn, "held_orders")
            for col_name, col_def in (
                ("discount_type", "TEXT NOT NULL DEFAULT ''"),
                ("discount_value", "INTEGER NOT NULL DEFAULT 0"),
                ("discount_minor", "INTEGER NOT NULL DEFAULT 0"),
                ("discount_label", "TEXT NOT NULL DEFAULT ''"),
            ):
                if col_name not in held_cols:
                    conn.execute(f"ALTER TABLE held_orders ADD COLUMN {col_name} {col_def};")

            held_item_cols = _table_columns(conn, "held_order_items")
            for col_name, col_def in (
                ("discount_type", "TEXT NOT NULL DEFAULT ''"),
                ("discount_value", "INTEGER NOT NULL DEFAULT 0"),
                ("discount_minor", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if col_name not in held_item_cols:
                    conn.execute(f"ALTER TABLE held_order_items ADD COLUMN {col_name} {col_def};")

            # Schema v8 new operational, inventory & expense tables
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS cashier_shifts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shift_number TEXT NOT NULL DEFAULT '',
                    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    cashier_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED')),
                    currency TEXT NOT NULL DEFAULT 'USD',
                    opening_cash_minor INTEGER NOT NULL DEFAULT 0 CHECK (opening_cash_minor >= 0),
                    counted_cash_minor INTEGER NOT NULL DEFAULT 0,
                    expected_cash_minor INTEGER NOT NULL DEFAULT 0,
                    discrepancy_minor INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    opened_at TEXT NOT NULL DEFAULT '',
                    closed_at TEXT NOT NULL DEFAULT '',
                    closed_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    closed_by_name TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS shift_cash_movements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shift_id INTEGER NOT NULL REFERENCES cashier_shifts(id) ON DELETE CASCADE,
                    movement_type TEXT NOT NULL CHECK (movement_type IN ('CASH_IN', 'CASH_OUT')),
                    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
                    currency TEXT NOT NULL DEFAULT 'USD',
                    reason TEXT NOT NULL DEFAULT '',
                    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    actor_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS order_refunds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    refund_number TEXT NOT NULL DEFAULT '',
                    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    shift_id INTEGER REFERENCES cashier_shifts(id) ON DELETE SET NULL,
                    refund_type TEXT NOT NULL DEFAULT 'PARTIAL' CHECK (refund_type IN ('FULL', 'PARTIAL')),
                    refund_method TEXT NOT NULL CHECK (
                        refund_method IN ('CASH', 'APPLE_PAY', 'INSTAPAY', 'EWALLET', 'CARD', 'CUSTOM', 'EXTERNAL')
                    ),
                    refund_amount_minor INTEGER NOT NULL CHECK (refund_amount_minor > 0),
                    currency TEXT NOT NULL DEFAULT 'USD',
                    reason TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    refund_status TEXT NOT NULL DEFAULT 'CASH_RETURNED' CHECK (
                        refund_status IN ('CASH_RETURNED', 'RECORDED_MANUAL_EXTERNAL')
                    ),
                    external_reference TEXT NOT NULL DEFAULT '',
                    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    actor_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS order_refund_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    refund_id INTEGER NOT NULL REFERENCES order_refunds(id) ON DELETE CASCADE,
                    order_item_id INTEGER NOT NULL REFERENCES order_items(id) ON DELETE CASCADE,
                    product_id INTEGER,
                    product_name TEXT NOT NULL DEFAULT '',
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    refund_amount_minor INTEGER NOT NULL CHECK (refund_amount_minor >= 0),
                    stock_action TEXT NOT NULL DEFAULT 'NONE' CHECK (stock_action IN ('NONE', 'RETURNED', 'WASTE'))
                );

                CREATE TABLE IF NOT EXISTS ingredients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name_en TEXT NOT NULL DEFAULT '',
                    name_ar TEXT NOT NULL DEFAULT '',
                    unit TEXT NOT NULL CHECK (unit IN ('g', 'kg', 'ml', 'l', 'pcs')),
                    stock_qty_milli INTEGER NOT NULL DEFAULT 0,
                    min_threshold_milli INTEGER NOT NULL DEFAULT 0 CHECK (min_threshold_milli >= 0),
                    cost_per_unit_minor INTEGER NOT NULL DEFAULT 0 CHECK (cost_per_unit_minor >= 0),
                    track_stock INTEGER NOT NULL DEFAULT 1,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    is_archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS product_recipes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
                    qty_milli INTEGER NOT NULL CHECK (qty_milli > 0),
                    UNIQUE(product_id, ingredient_id)
                );

                CREATE TABLE IF NOT EXISTS modifier_option_recipes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    modifier_option_id INTEGER NOT NULL REFERENCES modifier_options(id) ON DELETE CASCADE,
                    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
                    qty_milli INTEGER NOT NULL CHECK (qty_milli > 0),
                    UNIQUE(modifier_option_id, ingredient_id)
                );

                CREATE TABLE IF NOT EXISTS stock_movements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
                    movement_type TEXT NOT NULL CHECK (
                        movement_type IN ('RESTOCK', 'ADJUSTMENT', 'SALE_DEDUCTION', 'WASTE', 'REFUND_RETURN')
                    ),
                    delta_qty_milli INTEGER NOT NULL,
                    balance_after_milli INTEGER NOT NULL,
                    unit_cost_minor INTEGER NOT NULL DEFAULT 0,
                    reference_type TEXT NOT NULL DEFAULT '',
                    reference_id TEXT NOT NULL DEFAULT '',
                    client_deduction_key TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    actor_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL CHECK (
                        category IN ('RENT', 'ELECTRICITY', 'UTILITIES', 'SUPPLIES', 'MEALS', 'CLEANING', 'MAINTENANCE', 'MARKETING', 'OTHER')
                    ),
                    title TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
                    currency TEXT NOT NULL,
                    expense_date TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_by_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

            conn.executescript(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_client_tx_key
                    ON orders(client_tx_key) WHERE client_tx_key != '';
                CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_receipt_number
                    ON orders(receipt_number) WHERE receipt_number != '';
                CREATE INDEX IF NOT EXISTS idx_orders_shift_id
                    ON orders(shift_id);
                CREATE INDEX IF NOT EXISTS idx_payments_order_id
                    ON payments(order_id);
                CREATE INDEX IF NOT EXISTS idx_modifier_options_group
                    ON modifier_options(group_id, display_order, id);
                CREATE INDEX IF NOT EXISTS idx_order_item_modifiers_item
                    ON order_item_modifiers(order_item_id);
                CREATE INDEX IF NOT EXISTS idx_held_orders_status
                    ON held_orders(status, updated_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_held_orders_completed_order
                    ON held_orders(completed_order_id) WHERE completed_order_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_shifts_user_status
                    ON cashier_shifts(user_id, status, opened_at);
                CREATE INDEX IF NOT EXISTS idx_shift_cash_movements_shift
                    ON shift_cash_movements(shift_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_order_refunds_order
                    ON order_refunds(order_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_order_refunds_shift
                    ON order_refunds(shift_id);
                CREATE INDEX IF NOT EXISTS idx_stock_movements_ing
                    ON stock_movements(ingredient_id, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_deduction_key
                    ON stock_movements(client_deduction_key) WHERE client_deduction_key != '';
                CREATE INDEX IF NOT EXISTS idx_expenses_date
                    ON expenses(expense_date, category);
                """
            )

            existing_settings = conn.execute(
                "SELECT id FROM business_settings WHERE id = 1;"
            ).fetchone()
            if existing_settings is None:
                default_pm_json = serialize_payment_settings(
                    parse_payment_settings("", DEFAULT_BUSINESS_COUNTRY)
                )
                conn.execute(
                    """
                    INSERT INTO business_settings (
                        id, business_name, accent_color, currency, address, phone,
                        logo_blob, logo_ext, setup_completed, next_order_seq,
                        image_provider, pexels_api_key,
                        reduced_motion, auto_lock_minutes, backup_enabled,
                        backup_frequency, backup_destination, backup_retention_count, last_auto_backup_at,
                        app_language, receipt_language,
                        business_country, payment_methods_json,
                        auto_save_receipt_pdf, receipt_export_dir, online_translation_enabled,
                        country_configured
                    ) VALUES (1, ?, ?, ?, '', '', X'', '', 0, 1001, ?, '', 0, 0, 0, ?, '', ?, '', ?, ?, ?, ?, 0, '', 0, 0);
                    """,
                    (
                        DEFAULT_BUSINESS_NAME,
                        DEFAULT_ACCENT_COLOR,
                        DEFAULT_CURRENCY,
                        DEFAULT_IMAGE_PROVIDER,
                        DEFAULT_BACKUP_FREQUENCY,
                        DEFAULT_BACKUP_RETENTION,
                        DEFAULT_APP_LANGUAGE,
                        DEFAULT_RECEIPT_LANGUAGE,
                        DEFAULT_BUSINESS_COUNTRY,
                        default_pm_json,
                    ),
                )

            now = _utc_now_iso()
            # Seed canonical starter categories once (respects later admin edits/archives)
            cat_seeded_row = conn.execute(
                "SELECT value FROM app_meta WHERE key = 'seeded_categories_v5';"
            ).fetchone()
            if cat_seeded_row is None:
                for cat_en, cat_ar, order_val, is_sys in DEFAULT_SYSTEM_CATEGORIES:
                    nkey = normalize_category_key(cat_en)
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO categories (
                            normalized_key, name_en, name_ar, display_order,
                            is_system, is_archived, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 0, ?, ?);
                        """,
                        (nkey, cat_en, cat_ar, order_val, is_sys, now, now),
                    )
                conn.execute(
                    "INSERT INTO app_meta (key, value) VALUES ('seeded_categories_v5', '1');"
                )

            # Seed default sample menu on fresh installation
            seeded_row = conn.execute(
                "SELECT value FROM app_meta WHERE key = 'seeded_defaults';"
            ).fetchone()
            if seeded_row is None:
                cat_rows = conn.execute("SELECT id, name_en FROM categories;").fetchall()
                cat_id_by_key = {
                    normalize_category_key(r["name_en"]): int(r["id"]) for r in cat_rows
                }
                conn.executemany(
                    """
                    INSERT INTO products (
                        name, category, price_minor, created_at, updated_at,
                        name_en, name_ar, category_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    [
                        (
                            name,
                            cat,
                            price,
                            now,
                            now,
                            name,
                            DEFAULT_PRODUCT_AR_NAMES.get(name, ""),
                            cat_id_by_key.get(normalize_category_key(cat)),
                        )
                        for name, cat, price in DEFAULT_PRODUCTS
                    ],
                )
                conn.execute(
                    "INSERT INTO app_meta (key, value) VALUES ('seeded_defaults', '1');"
                )

            # Backfill category_id & bilingual product columns for migrated v1-v4 rows safely
            unlinked_prods = conn.execute(
                "SELECT id, name, category, name_en, name_ar, category_id FROM products;"
            ).fetchall()
            for pr in unlinked_prods:
                pid = int(pr["id"])
                new_cid = pr["category_id"]
                raw_cat = (pr["category"] or "").strip() or "Other"
                if new_cid is None:
                    cat_rec = self._resolve_or_create_category_in_tx(
                        conn,
                        raw_cat,
                        create_missing=True,
                        require_perm=False,
                    )
                    new_cid = cat_rec.id

                cur_en = (pr["name_en"] or "").strip()
                cur_ar = (pr["name_ar"] or "").strip()
                updates: list[str] = []
                params_u: list[object] = []
                if pr["category_id"] is None and new_cid is not None:
                    updates.append("category_id = ?")
                    params_u.append(new_cid)
                if not cur_en and not cur_ar:
                    pname = (pr["name"] or "").strip()
                    if pname in DEFAULT_PRODUCT_AR_NAMES:
                        updates.append("name_en = ?")
                        updates.append("name_ar = ?")
                        params_u.extend([pname, DEFAULT_PRODUCT_AR_NAMES[pname]])
                    elif detect_primary_script(pname) == "ar":
                        updates.append("name_ar = ?")
                        params_u.append(pname)
                    else:
                        updates.append("name_en = ?")
                        params_u.append(pname)
                if updates:
                    params_u.append(pid)
                    conn.execute(
                        f"UPDATE products SET {', '.join(updates)} WHERE id = ?;",
                        params_u,
                    )

            # Backfill legacy v1..v4 orders cleanly without altering dates/totals/cashiers
            conn.execute(
                "UPDATE orders SET receipt_number = order_number WHERE receipt_number = '';"
            )
            legacy_no_pay = conn.execute(
                """
                SELECT o.id, o.total_minor, o.currency, o.created_at,
                       o.cashier_user_id, o.cashier_name_snapshot
                FROM orders o
                LEFT JOIN payments p ON p.order_id = o.id
                WHERE p.id IS NULL;
                """
            ).fetchall()
            for lo in legacy_no_pay:
                conn.execute(
                    """
                    INSERT INTO payments (
                        order_id, payment_method, payment_provider, custom_method_label,
                        payment_amount_minor, currency, payment_status, external_reference,
                        confirmed_by_user_id, confirmed_by_name, confirmed_at, verification_source
                    ) VALUES (?, ?, '', '', ?, ?, ?, '', ?, ?, ?, ?);
                    """,
                    (
                        int(lo["id"]),
                        METHOD_CASH,
                        int(lo["total_minor"]),
                        validate_currency(lo["currency"]),
                        STATUS_CASH_SETTLED,
                        lo["cashier_user_id"],
                        lo["cashier_name_snapshot"] or "",
                        lo["created_at"],
                        VERIFICATION_CASH_DRAWER,
                    ),
                )

            conn.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('schema_version', ?);",
                (str(CURRENT_SCHEMA_VERSION),),
            )

    def _has_users(self, conn: sqlite3.Connection) -> bool:
        return conn.execute("SELECT 1 FROM users LIMIT 1;").fetchone() is not None

    def _resolve_actor(
        self,
        conn: sqlite3.Connection,
        actor: UserAccount | None,
        permission: Permission | None,
        message: str = "",
    ) -> UserAccount | None:
        effective = actor if actor is not None else self._session_actor
        has_users = self._has_users(conn)

        if effective is None:
            if has_users:
                raise AuthorizationError(message or tr("backend.err.auth_required"))
            return None

        row = conn.execute("SELECT * FROM users WHERE id = ?;", (int(effective.id),)).fetchone()
        if row is None:
            raise AuthorizationError(tr("backend.err.signed_in_missing"))
        live_user = _row_to_user(row)
        if permission is None:
            return live_user
        return require_permission(live_user, permission, message=message)

    def _write_audit(
        self,
        conn: sqlite3.Connection,
        action: str,
        *,
        actor: UserAccount | None = None,
        actor_username_override: str = "",
        actor_role_override: str = "",
        target_type: str = "",
        target_id: str = "",
        details: str = "",
    ) -> None:
        actor_id = actor.id if actor is not None else None
        if actor_id is not None:
            row_exists = conn.execute(
                "SELECT 1 FROM users WHERE id = ?;", (int(actor_id),)
            ).fetchone()
            if row_exists is None:
                actor_id = None
        actor_username = (
            actor_username_override
            if actor_username_override
            else (actor.username if actor is not None else "")
        )
        actor_role = (
            actor_role_override
            if actor_role_override
            else (actor.role if actor is not None else "")
        )
        conn.execute(
            """
            INSERT INTO audit_logs (
                created_at, actor_user_id, actor_username, actor_role,
                action, target_type, target_id, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                _utc_now_iso(),
                actor_id,
                (actor_username or "")[:64],
                (actor_role or "")[:24],
                action[:64],
                target_type[:48],
                str(target_id)[:64],
                (details or "")[:300],
            ),
        )

    def record_audit_event(
        self,
        action: str,
        *,
        target_type: str = "",
        target_id: str = "",
        details: str = "",
        actor: UserAccount | None = None,
        actor_username_override: str = "",
        actor_role_override: str = "",
    ) -> None:
        effective = actor if actor is not None else self._session_actor
        with self._tx() as conn:
            self._write_audit(
                conn,
                action,
                actor=effective,
                actor_username_override=actor_username_override,
                actor_role_override=actor_role_override,
                target_type=target_type,
                target_id=target_id,
                details=details,
            )

    def list_audit_logs(
        self, limit: int = 200, *, actor: UserAccount | None = None
    ) -> list[AuditEvent]:
        safe_limit = max(1, min(1000, int(limit)))
        with self._tx() as conn:
            self._resolve_actor(conn, actor, Permission.MANAGE_USERS)
            rows = conn.execute(
                "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?;",
                (safe_limit,),
            ).fetchall()
        return [
            AuditEvent(
                id=int(r["id"]),
                created_at=r["created_at"],
                actor_user_id=int(r["actor_user_id"]) if r["actor_user_id"] is not None else None,
                actor_username=r["actor_username"] or "",
                actor_role=r["actor_role"] or "",
                action=r["action"],
                target_type=r["target_type"] or "",
                target_id=r["target_id"] or "",
                details=r["details"] or "",
            )
            for r in rows
        ]

    # ---------------- Authentication & Account Management ----------------

    def has_admin(self) -> bool:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM users WHERE role = 'ADMIN' AND is_active = 1;"
            ).fetchone()
            return bool(row and int(row["cnt"]) > 0)

    def has_any_users(self) -> bool:
        with self._tx() as conn:
            return self._has_users(conn)

    def active_admin_count(self) -> int:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM users WHERE role = 'ADMIN' AND is_active = 1;"
            ).fetchone()
            return int(row["cnt"]) if row else 0

    def initialize_owner_and_business(
        self,
        *,
        business_cfg: BusinessConfig,
        admin_username: str,
        admin_password: str,
        admin_display_name: str = "",
    ) -> tuple[BusinessConfig, UserAccount]:
        """Atomically configure storefront settings & create the first administrator."""
        clean_user = validate_username(admin_username)
        clean_disp = validate_display_name(admin_display_name, clean_user)
        validate_password_policy(admin_password)

        biz_name = (business_cfg.business_name or "").strip()[:60]
        if not biz_name:
            raise ValueError(tr("backend.err.business_empty"))

        pw_hash = hash_password(admin_password)
        now = _utc_now_iso()

        with self._tx() as conn:
            existing_admin = conn.execute(
                "SELECT COUNT(*) AS cnt FROM users WHERE role = 'ADMIN' AND is_active = 1;"
            ).fetchone()
            if existing_admin and int(existing_admin["cnt"]) > 0:
                raise AuthorizationError(tr("backend.err.admin_already_exists"))

            dup = conn.execute(
                "SELECT id FROM users WHERE username = ?;", (clean_user,)
            ).fetchone()
            if dup is not None:
                raise ValueError(tr("backend.err.username_exists", username=clean_user))

            cur = conn.execute(
                """
                INSERT INTO users (
                    username, display_name, role, password_hash, is_active,
                    created_at, updated_at, last_login_at, preferred_language
                ) VALUES (?, ?, 'ADMIN', ?, 1, ?, ?, ?, '');
                """,
                (clean_user, clean_disp, pw_hash, now, now, now),
            )
            admin_id = int(cur.lastrowid)

            clean_cfg = self._prepare_settings(business_cfg)
            clean_cfg.setup_completed = True
            self._write_settings_row(conn, clean_cfg)

            admin_user = UserAccount(
                id=admin_id,
                username=clean_user,
                display_name=clean_disp,
                role=ROLE_ADMIN,
                is_active=True,
                created_at=now,
                updated_at=now,
                last_login_at=now,
            )
            self._write_audit(
                conn,
                "ADMIN_INITIALIZED",
                actor=admin_user,
                target_type="user",
                target_id=str(admin_id),
                details=f"Initial administrator '{clean_user}' created for '{clean_cfg.business_name}'",
            )
        return clean_cfg, admin_user

    def authenticate(
        self, username: str, password: str, *, now: datetime | None = None
    ) -> UserAccount:
        raw_user = (username or "").strip()
        if not raw_user or not password:
            raise AuthenticationError(tr("backend.err.enter_both_creds"))

        now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        now_iso = _utc_now_iso(now_utc)
        user_key = raw_user.lower()

        auth_error: AuthenticationError | None = None
        authenticated_user: UserAccount | None = None

        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?;",
                (raw_user,),
            ).fetchone()

            if row is None:
                attempts, locked_iso = self._unknown_login_attempts.get(user_key, (0, ""))
                locked_dt = _parse_iso_to_utc(locked_iso)
                if locked_dt is not None and locked_dt > now_utc:
                    rem = max(1, int((locked_dt - now_utc).total_seconds()))
                    self._write_audit(
                        conn,
                        "AUTH_LOGIN_FAILED",
                        actor_username_override=raw_user,
                        target_type="auth",
                        details="Throttled sign-in attempt for unknown username",
                    )
                    auth_error = AuthenticationError(
                        tr("backend.err.throttled", seconds=rem),
                        locked_seconds=rem,
                    )
                else:
                    dummy_verify(password)
                    attempts += 1
                    lock_sec = throttle_duration_seconds(attempts)
                    new_locked_iso = (
                        _utc_now_iso(now_utc + timedelta(seconds=lock_sec))
                        if lock_sec > 0
                        else ""
                    )
                    if len(self._unknown_login_attempts) >= 256:
                        self._unknown_login_attempts.clear()
                    self._unknown_login_attempts[user_key] = (attempts, new_locked_iso)

                    self._write_audit(
                        conn,
                        "AUTH_LOGIN_FAILED",
                        actor_username_override=raw_user,
                        target_type="auth",
                        details="Invalid credentials",
                    )
                    if lock_sec > 0:
                        auth_error = AuthenticationError(
                            tr("backend.err.throttled", seconds=lock_sec),
                            locked_seconds=lock_sec,
                        )
                    else:
                        auth_error = AuthenticationError(tr("backend.err.invalid_credentials"))
            else:
                locked_dt = _parse_iso_to_utc(row["locked_until"] or "")
                if locked_dt is not None and locked_dt > now_utc:
                    rem = max(1, int((locked_dt - now_utc).total_seconds()))
                    self._write_audit(
                        conn,
                        "AUTH_LOGIN_FAILED",
                        actor_username_override=row["username"],
                        actor_role_override=row["role"],
                        target_type="user",
                        target_id=str(row["id"]),
                        details=f"Sign-in rejected while throttled ({rem}s remaining)",
                    )
                    auth_error = AuthenticationError(
                        tr("backend.err.throttled", seconds=rem),
                        locked_seconds=rem,
                    )
                else:
                    ok = verify_password(row["password_hash"], password)
                    if not ok:
                        new_attempts = int(row["failed_attempts"] or 0) + 1
                        lock_sec = throttle_duration_seconds(new_attempts)
                        new_locked_iso = (
                            _utc_now_iso(now_utc + timedelta(seconds=lock_sec))
                            if lock_sec > 0
                            else ""
                        )
                        conn.execute(
                            "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?;",
                            (new_attempts, new_locked_iso, int(row["id"])),
                        )
                        self._write_audit(
                            conn,
                            "AUTH_LOGIN_FAILED",
                            actor_username_override=row["username"],
                            actor_role_override=row["role"],
                            target_type="user",
                            target_id=str(row["id"]),
                            details=f"Incorrect password (attempt {new_attempts})",
                        )
                        if lock_sec > 0:
                            auth_error = AuthenticationError(
                                tr("backend.err.throttled", seconds=lock_sec),
                                locked_seconds=lock_sec,
                            )
                        else:
                            auth_error = AuthenticationError(tr("backend.err.invalid_credentials"))
                    elif not bool(row["is_active"]):
                        self._write_audit(
                            conn,
                            "AUTH_LOGIN_FAILED",
                            actor_username_override=row["username"],
                            actor_role_override=row["role"],
                            target_type="user",
                            target_id=str(row["id"]),
                            details="Sign-in rejected for disabled account",
                        )
                        auth_error = AuthenticationError(tr("backend.err.account_disabled"))
                    else:
                        updated_hash = (
                            hash_password(password)
                            if needs_rehash(row["password_hash"])
                            else row["password_hash"]
                        )
                        conn.execute(
                            """
                            UPDATE users
                            SET password_hash = ?, failed_attempts = 0, locked_until = '', last_login_at = ?
                            WHERE id = ?;
                            """,
                            (updated_hash, now_iso, int(row["id"])),
                        )
                        self._unknown_login_attempts.pop(user_key, None)

                        fresh = conn.execute(
                            "SELECT * FROM users WHERE id = ?;", (int(row["id"]),)
                        ).fetchone()
                        authenticated_user = _row_to_user(fresh)
                        self._write_audit(
                            conn,
                            "AUTH_LOGIN_SUCCESS",
                            actor=authenticated_user,
                            target_type="user",
                            target_id=str(authenticated_user.id),
                            details=f"Signed in as {authenticated_user.role}",
                        )

        if auth_error is not None:
            raise auth_error
        assert authenticated_user is not None
        return authenticated_user

    def verify_user_password(self, user_id: int, password: str) -> bool:
        """Verify an existing active user's password without resetting session state."""
        if not password:
            return False
        with self._tx() as conn:
            row = conn.execute(
                "SELECT password_hash, is_active FROM users WHERE id = ?;",
                (int(user_id),),
            ).fetchone()
            if row is None or not bool(row["is_active"]):
                return False
            return verify_password(row["password_hash"], password)

    def list_users(self, *, actor: UserAccount | None = None) -> list[UserAccount]:
        with self._tx() as conn:
            self._resolve_actor(conn, actor, Permission.MANAGE_USERS)
            rows = conn.execute(
                """
                SELECT id, username, display_name, role, is_active, created_at, updated_at, last_login_at,
                       preferred_language, font_scale_pct, ui_density, reduced_motion_pref
                FROM users
                ORDER BY CASE WHEN role = 'ADMIN' THEN 0 ELSE 1 END, username COLLATE NOCASE ASC;
                """
            ).fetchall()
            return [_row_to_user(r) for r in rows]

    def create_user(
        self,
        username: str,
        password: str,
        role: str = ROLE_CASHIER,
        *,
        display_name: str = "",
        is_active: bool = True,
        preferred_language: str = "",
        actor: UserAccount | None = None,
    ) -> UserAccount:
        clean_user = validate_username(username)
        clean_role = validate_role(role)
        clean_disp = validate_display_name(display_name, clean_user)
        clean_lang = validate_preferred_language(preferred_language)
        pw_hash = hash_password(password)
        now = _utc_now_iso()

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_USERS)
            dup = conn.execute(
                "SELECT id FROM users WHERE username = ?;",
                (clean_user,),
            ).fetchone()
            if dup is not None:
                raise ValueError(tr("backend.err.username_exists", username=clean_user))

            cur = conn.execute(
                """
                INSERT INTO users (
                    username, display_name, role, password_hash, is_active,
                    created_at, updated_at, preferred_language
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    clean_user,
                    clean_disp,
                    clean_role,
                    pw_hash,
                    1 if is_active else 0,
                    now,
                    now,
                    clean_lang,
                ),
            )
            uid = int(cur.lastrowid)
            created_row = conn.execute("SELECT * FROM users WHERE id = ?;", (uid,)).fetchone()
            created = _row_to_user(created_row)
            self._write_audit(
                conn,
                "USER_CREATED",
                actor=live_actor,
                target_type="user",
                target_id=str(uid),
                details=f"Created user '{clean_user}' with role {clean_role}",
            )
            return created

    def update_user(
        self,
        user_id: int,
        *,
        username: str | None = None,
        display_name: str | None = None,
        role: str | None = None,
        preferred_language: str | None = None,
        actor: UserAccount | None = None,
    ) -> UserAccount:
        now = _utc_now_iso()
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_USERS)
            row = conn.execute("SELECT * FROM users WHERE id = ?;", (int(user_id),)).fetchone()
            if row is None:
                raise ValueError(tr("backend.err.user_not_found"))

            new_username = validate_username(username) if username is not None else row["username"]
            new_disp = (
                validate_display_name(display_name, new_username)
                if display_name is not None
                else (row["display_name"] or new_username)
            )
            new_role = validate_role(role) if role is not None else row["role"]
            existing_lang = (
                row["preferred_language"] if "preferred_language" in row.keys() else ""
            )
            new_lang = (
                validate_preferred_language(preferred_language)
                if preferred_language is not None
                else validate_preferred_language(existing_lang)
            )

            dup = conn.execute(
                "SELECT id FROM users WHERE username = ? AND id != ?;",
                (new_username, int(user_id)),
            ).fetchone()
            if dup is not None:
                raise ValueError(tr("backend.err.username_exists", username=new_username))

            # Prevent demoting the last active administrator
            if row["role"] == ROLE_ADMIN and new_role != ROLE_ADMIN and bool(row["is_active"]):
                other_admins = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM users WHERE role = 'ADMIN' AND is_active = 1 AND id != ?;",
                    (int(user_id),),
                ).fetchone()
                if not other_admins or int(other_admins["cnt"]) == 0:
                    raise ValueError(tr("backend.err.last_admin_demote"))

            conn.execute(
                """
                UPDATE users
                SET username = ?, display_name = ?, role = ?, preferred_language = ?, updated_at = ?
                WHERE id = ?;
                """,
                (new_username, new_disp, new_role, new_lang, now, int(user_id)),
            )
            updated_row = conn.execute("SELECT * FROM users WHERE id = ?;", (int(user_id),)).fetchone()
            updated = _row_to_user(updated_row)

            action_name = "USER_ROLE_CHANGED" if new_role != row["role"] else "USER_UPDATED"
            self._write_audit(
                conn,
                action_name,
                actor=live_actor,
                target_type="user",
                target_id=str(user_id),
                details=f"Updated '{updated.username}' (role={updated.role}, name='{updated.display_name}')",
            )
            return updated

    def reset_user_password(
        self,
        user_id: int,
        new_password: str,
        *,
        actor: UserAccount | None = None,
    ) -> None:
        pw_hash = hash_password(new_password)
        now = _utc_now_iso()
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_USERS)
            row = conn.execute("SELECT * FROM users WHERE id = ?;", (int(user_id),)).fetchone()
            if row is None:
                raise ValueError(tr("backend.err.user_not_found"))

            conn.execute(
                """
                UPDATE users
                SET password_hash = ?, failed_attempts = 0, locked_until = '', updated_at = ?
                WHERE id = ?;
                """,
                (pw_hash, now, int(user_id)),
            )
            self._write_audit(
                conn,
                "USER_PASSWORD_RESET",
                actor=live_actor,
                target_type="user",
                target_id=str(user_id),
                details=f"Reset password for '{row['username']}'",
            )

    def set_user_active(
        self,
        user_id: int,
        is_active: bool,
        *,
        actor: UserAccount | None = None,
    ) -> UserAccount:
        now = _utc_now_iso()
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_USERS)
            row = conn.execute("SELECT * FROM users WHERE id = ?;", (int(user_id),)).fetchone()
            if row is None:
                raise ValueError(tr("backend.err.user_not_found"))

            target_active = bool(is_active)
            if not target_active:
                if live_actor is not None and live_actor.id == int(user_id):
                    raise ValueError(tr("backend.err.self_disable"))
                if row["role"] == ROLE_ADMIN and bool(row["is_active"]):
                    other_admins = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM users WHERE role = 'ADMIN' AND is_active = 1 AND id != ?;",
                        (int(user_id),),
                    ).fetchone()
                    if not other_admins or int(other_admins["cnt"]) == 0:
                        raise ValueError(tr("backend.err.last_admin_disable"))

            conn.execute(
                """
                UPDATE users
                SET is_active = ?, failed_attempts = 0, locked_until = '', updated_at = ?
                WHERE id = ?;
                """,
                (1 if target_active else 0, now, int(user_id)),
            )
            updated_row = conn.execute("SELECT * FROM users WHERE id = ?;", (int(user_id),)).fetchone()
            updated = _row_to_user(updated_row)
            self._write_audit(
                conn,
                "USER_REACTIVATED" if target_active else "USER_DISABLED",
                actor=live_actor,
                target_type="user",
                target_id=str(user_id),
                details=f"{'Reactivated' if target_active else 'Disabled'} account '{updated.username}'",
            )
            return updated

    def set_user_preferred_language(
        self,
        user_id: int,
        preferred_language: str,
        *,
        actor: UserAccount | None = None,
    ) -> UserAccount:
        clean_lang = validate_preferred_language(preferred_language)
        now = _utc_now_iso()
        effective = actor if actor is not None else self._session_actor
        with self._tx() as conn:
            if effective is None and self._has_users(conn):
                raise AuthorizationError(tr("backend.err.auth_required"))
            if effective is not None and effective.id != int(user_id):
                self._resolve_actor(conn, effective, Permission.MANAGE_USERS)
            row = conn.execute("SELECT * FROM users WHERE id = ?;", (int(user_id),)).fetchone()
            if row is None:
                raise ValueError(tr("backend.err.user_not_found"))
            conn.execute(
                "UPDATE users SET preferred_language = ?, updated_at = ? WHERE id = ?;",
                (clean_lang, now, int(user_id)),
            )
            updated_row = conn.execute("SELECT * FROM users WHERE id = ?;", (int(user_id),)).fetchone()
            return _row_to_user(updated_row)

    def update_my_preferences(
        self,
        *,
        preferred_language: str | None = None,
        font_scale_pct: int | None = None,
        ui_density: str | None = None,
        reduced_motion_pref: int | None = None,
        user_id: int | None = None,
        actor: UserAccount | None = None,
    ) -> UserAccount:
        """Allow Cashier or Admin to update their own language & display preferences."""
        effective = actor if actor is not None else self._session_actor
        now = _utc_now_iso()
        with self._tx() as conn:
            if effective is None:
                raise AuthorizationError(tr("backend.err.auth_required"))
            target_id = int(user_id) if user_id is not None else int(effective.id)
            if target_id != int(effective.id):
                self._resolve_actor(conn, effective, Permission.MANAGE_USERS)
            row = conn.execute("SELECT * FROM users WHERE id = ?;", (target_id,)).fetchone()
            if row is None:
                raise ValueError(tr("backend.err.user_not_found"))

            new_lang = (
                validate_preferred_language(preferred_language)
                if preferred_language is not None
                else validate_preferred_language(row["preferred_language"] if "preferred_language" in row.keys() else "")
            )
            new_scale = (
                validate_font_scale_pct(font_scale_pct)
                if font_scale_pct is not None
                else validate_font_scale_pct(row["font_scale_pct"] if "font_scale_pct" in row.keys() else 100)
            )
            new_density = (
                validate_ui_density(ui_density)
                if ui_density is not None
                else validate_ui_density(row["ui_density"] if "ui_density" in row.keys() else "comfortable")
            )
            if reduced_motion_pref is not None:
                rm_val = int(reduced_motion_pref)
                if rm_val not in (-1, 0, 1):
                    rm_val = -1
            else:
                raw_rm = row["reduced_motion_pref"] if "reduced_motion_pref" in row.keys() else -1
                rm_val = int(raw_rm) if int(raw_rm) in (-1, 0, 1) else -1

            conn.execute(
                """
                UPDATE users
                SET preferred_language = ?, font_scale_pct = ?, ui_density = ?, reduced_motion_pref = ?, updated_at = ?
                WHERE id = ?;
                """,
                (new_lang, new_scale, new_density, rm_val, now, target_id),
            )
            updated_row = conn.execute("SELECT * FROM users WHERE id = ?;", (target_id,)).fetchone()
            updated_user = _row_to_user(updated_row)
            if self._session_actor is not None and self._session_actor.id == updated_user.id:
                self._session_actor = updated_user
            return updated_user

    def save_window_state_pref(
        self,
        *,
        window_state_pref: str,
        window_width: int,
        window_height: int,
    ) -> None:
        """Persist preferred window state (maximized/normal) & last normal size cleanly."""
        clean_state = validate_window_state_pref(window_state_pref)
        clean_w = validate_window_dimension(window_width, default=1280, min_val=900, max_val=4096)
        clean_h = validate_window_dimension(window_height, default=800, min_val=485, max_val=2560)
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE business_settings
                SET window_state_pref = ?, window_width = ?, window_height = ?
                WHERE id = 1;
                """,
                (clean_state, clean_w, clean_h),
            )

    # ---------------- Business Settings ----------------

    def get_settings(self) -> BusinessConfig:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM business_settings WHERE id = 1;").fetchone()
            return _row_to_settings(row)

    @staticmethod
    def _prepare_settings(cfg: BusinessConfig) -> BusinessConfig:
        name = (cfg.business_name or "").strip()[:60]
        if not name:
            raise ValueError(tr("backend.err.business_empty"))
        country = validate_country_code(cfg.business_country)
        parsed_pm = parse_payment_settings(cfg.payment_methods_json, country)
        pm_json = serialize_payment_settings(parsed_pm)
        return BusinessConfig(
            business_name=name,
            accent_color=validate_hex_color(cfg.accent_color),
            currency=validate_currency(cfg.currency),
            address=(cfg.address or "").strip()[:140],
            phone=(cfg.phone or "").strip()[:40],
            logo_blob=bytes(cfg.logo_blob or b""),
            logo_ext=(cfg.logo_ext or "").strip()[:10],
            setup_completed=bool(cfg.setup_completed),
            image_provider=validate_image_provider(cfg.image_provider),
            pexels_api_key=(cfg.pexels_api_key or "").strip()[:256],
            reduced_motion=bool(cfg.reduced_motion),
            auto_lock_minutes=validate_auto_lock_minutes(cfg.auto_lock_minutes),
            backup_enabled=bool(cfg.backup_enabled),
            backup_frequency=validate_backup_frequency(cfg.backup_frequency),
            backup_destination=(cfg.backup_destination or "").strip()[:400],
            backup_retention_count=validate_retention_count(cfg.backup_retention_count),
            last_auto_backup_at=(cfg.last_auto_backup_at or "").strip()[:64],
            app_language=validate_app_language(cfg.app_language),
            receipt_language=validate_receipt_language(cfg.receipt_language),
            business_country=country,
            payment_methods_json=pm_json,
            auto_save_receipt_pdf=bool(cfg.auto_save_receipt_pdf),
            receipt_export_dir=(cfg.receipt_export_dir or "").strip()[:400],
            online_translation_enabled=bool(cfg.online_translation_enabled),
            country_configured=bool(cfg.country_configured),
            window_state_pref=validate_window_state_pref(cfg.window_state_pref),
            window_width=validate_window_dimension(
                cfg.window_width, default=1280, min_val=900, max_val=4096
            ),
            window_height=validate_window_dimension(
                cfg.window_height, default=800, min_val=485, max_val=2560
            ),
            default_font_scale_pct=validate_font_scale_pct(cfg.default_font_scale_pct),
            default_ui_density=validate_ui_density(cfg.default_ui_density),
            allow_cashier_discounts=bool(cfg.allow_cashier_discounts),
            allow_cashier_refunds=bool(cfg.allow_cashier_refunds),
            max_cashier_discount_pct=validate_discount_pct_cap(cfg.max_cashier_discount_pct),
            default_order_type=validate_default_order_type_cfg(cfg.default_order_type),
            enable_dine_in=bool(cfg.enable_dine_in),
            enable_takeaway=bool(cfg.enable_takeaway),
            enable_delivery=bool(cfg.enable_delivery),
            modifiers_enabled=bool(cfg.modifiers_enabled),
            inventory_tracking_enabled=bool(cfg.inventory_tracking_enabled),
            auto_deduct_inventory=bool(cfg.auto_deduct_inventory),
            default_low_stock_alert=validate_low_stock_alert_threshold(cfg.default_low_stock_alert),
            require_shift_for_sales=bool(cfg.require_shift_for_sales),
            require_opening_cash=bool(cfg.require_opening_cash),
            show_cashier_on_receipt=bool(cfg.show_cashier_on_receipt),
            show_business_details_on_receipt=bool(cfg.show_business_details_on_receipt),
            printer_name=(cfg.printer_name or "").strip()[:160],
            printer_paper_width_mm=validate_thermal_width_mm(cfg.printer_paper_width_mm),
            auto_print_receipt=bool(cfg.auto_print_receipt),
        )

    @staticmethod
    def _write_settings_row(conn: sqlite3.Connection, clean: BusinessConfig) -> None:
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
                setup_completed = ?,
                image_provider = ?,
                pexels_api_key = ?,
                reduced_motion = ?,
                auto_lock_minutes = ?,
                backup_enabled = ?,
                backup_frequency = ?,
                backup_destination = ?,
                backup_retention_count = ?,
                last_auto_backup_at = ?,
                app_language = ?,
                receipt_language = ?,
                business_country = ?,
                payment_methods_json = ?,
                auto_save_receipt_pdf = ?,
                receipt_export_dir = ?,
                online_translation_enabled = ?,
                country_configured = ?,
                window_state_pref = ?,
                window_width = ?,
                window_height = ?,
                default_font_scale_pct = ?,
                default_ui_density = ?,
                allow_cashier_discounts = ?,
                allow_cashier_refunds = ?,
                max_cashier_discount_pct = ?,
                default_order_type = ?,
                enable_dine_in = ?,
                enable_takeaway = ?,
                enable_delivery = ?,
                modifiers_enabled = ?,
                inventory_tracking_enabled = ?,
                auto_deduct_inventory = ?,
                default_low_stock_alert = ?,
                require_shift_for_sales = ?,
                require_opening_cash = ?,
                show_cashier_on_receipt = ?,
                show_business_details_on_receipt = ?,
                printer_name = ?,
                printer_paper_width_mm = ?,
                auto_print_receipt = ?
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
                clean.image_provider,
                clean.pexels_api_key,
                1 if clean.reduced_motion else 0,
                clean.auto_lock_minutes,
                1 if clean.backup_enabled else 0,
                clean.backup_frequency,
                clean.backup_destination,
                clean.backup_retention_count,
                clean.last_auto_backup_at,
                clean.app_language,
                clean.receipt_language,
                clean.business_country,
                clean.payment_methods_json,
                1 if clean.auto_save_receipt_pdf else 0,
                clean.receipt_export_dir,
                1 if clean.online_translation_enabled else 0,
                1 if clean.country_configured else 0,
                clean.window_state_pref,
                clean.window_width,
                clean.window_height,
                clean.default_font_scale_pct,
                clean.default_ui_density,
                1 if clean.allow_cashier_discounts else 0,
                1 if clean.allow_cashier_refunds else 0,
                clean.max_cashier_discount_pct,
                clean.default_order_type,
                1 if clean.enable_dine_in else 0,
                1 if clean.enable_takeaway else 0,
                1 if clean.enable_delivery else 0,
                1 if clean.modifiers_enabled else 0,
                1 if clean.inventory_tracking_enabled else 0,
                1 if clean.auto_deduct_inventory else 0,
                clean.default_low_stock_alert,
                1 if clean.require_shift_for_sales else 0,
                1 if clean.require_opening_cash else 0,
                1 if clean.show_cashier_on_receipt else 0,
                1 if clean.show_business_details_on_receipt else 0,
                clean.printer_name,
                clean.printer_paper_width_mm,
                1 if clean.auto_print_receipt else 0,
            ),
        )

    def set_app_language(self, lang: str) -> str:
        """Persist active UI/storefront language (safe pre-login on Setup/Login views)."""
        clean_lang = validate_app_language(lang)
        with self._tx() as conn:
            conn.execute(
                "UPDATE business_settings SET app_language = ? WHERE id = 1;",
                (clean_lang,),
            )
        return clean_lang

    def save_settings(
        self, cfg: BusinessConfig, *, actor: UserAccount | None = None
    ) -> BusinessConfig:
        clean = self._prepare_settings(cfg)
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_SETTINGS)
            self._write_settings_row(conn, clean)
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "SETTINGS_UPDATED",
                    actor=live_actor,
                    target_type="settings",
                    target_id="1",
                    details=(
                        f"Updated business settings (country={clean.business_country}, "
                        f"currency={clean.currency}, auto_lock={clean.auto_lock_minutes}m, "
                        f"auto_backup={clean.backup_enabled})"
                    ),
                )
        return clean

    def mark_auto_backup_completed(self, timestamp_iso: str) -> None:
        """Internal system update recording last automatic backup timestamp."""
        with self._tx() as conn:
            conn.execute(
                "UPDATE business_settings SET last_auto_backup_at = ? WHERE id = 1;",
                ((timestamp_iso or "").strip()[:64],),
            )

    # ---------------- Translation Cache & Smart Categories ----------------

    @staticmethod
    def _make_tx_cache_lookup(
        conn: sqlite3.Connection,
    ) -> Callable[[str, str, str], tuple[str, bool, str] | None]:
        def _lookup(source_text: str, source_lang: str, target_lang: str) -> tuple[str, bool, str] | None:
            src_clean = " ".join((source_text or "").strip().split())
            if not src_clean:
                return None
            row = conn.execute(
                """
                SELECT target_text, is_user_confirmed, confidence
                FROM translation_cache
                WHERE source_text = ? COLLATE NOCASE AND source_lang = ? AND target_lang = ?
                LIMIT 1;
                """,
                (src_clean, source_lang, target_lang),
            ).fetchone()
            if row is None:
                return None
            return (str(row["target_text"]), bool(row["is_user_confirmed"]), str(row["confidence"]))

        return _lookup

    def get_cached_translation(
        self, source_text: str, source_lang: str, target_lang: str
    ) -> tuple[str, bool, str] | None:
        src_clean = " ".join((source_text or "").strip().split())
        if not src_clean:
            return None
        with self._tx() as conn:
            return self._make_tx_cache_lookup(conn)(src_clean, source_lang, target_lang)

    @staticmethod
    def _upsert_translation_cache_in_tx(
        conn: sqlite3.Connection,
        source_text: str,
        source_lang: str,
        target_text: str,
        target_lang: str,
        *,
        confidence: str = "medium",
        source: str = "offline_dict",
        is_user_confirmed: bool = False,
    ) -> None:
        src_clean = " ".join((source_text or "").strip().split())[:120]
        dst_clean = " ".join((target_text or "").strip().split())[:120]
        if not src_clean or not dst_clean:
            return
        if source_lang not in {"en", "ar"} or target_lang not in {"en", "ar"} or source_lang == target_lang:
            return
        now = _utc_now_iso()
        existing = conn.execute(
            """
            SELECT id, is_user_confirmed
            FROM translation_cache
            WHERE source_text = ? AND source_lang = ? AND target_lang = ?;
            """,
            (src_clean, source_lang, target_lang),
        ).fetchone()
        if existing is not None:
            # Never silently overwrite a user-confirmed translation with an auto suggestion
            if bool(existing["is_user_confirmed"]) and not is_user_confirmed:
                return
            conn.execute(
                """
                UPDATE translation_cache
                SET target_text = ?, confidence = ?, source = ?, is_user_confirmed = ?, updated_at = ?
                WHERE id = ?;
                """,
                (
                    dst_clean,
                    "confirmed" if is_user_confirmed else confidence,
                    "user_cache" if is_user_confirmed else source,
                    1 if is_user_confirmed else 0,
                    now,
                    int(existing["id"]),
                ),
            )
            return

        conn.execute(
            """
            INSERT INTO translation_cache (
                source_text, source_lang, target_text, target_lang,
                confidence, source, is_user_confirmed, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                src_clean,
                source_lang,
                dst_clean,
                target_lang,
                "confirmed" if is_user_confirmed else confidence,
                "user_cache" if is_user_confirmed else source,
                1 if is_user_confirmed else 0,
                now,
                now,
            ),
        )

    def upsert_translation_cache(
        self,
        source_text: str,
        source_lang: str,
        target_text: str,
        target_lang: str,
        *,
        confidence: str = "medium",
        source: str = "offline_dict",
        is_user_confirmed: bool = False,
    ) -> None:
        with self._tx() as conn:
            self._upsert_translation_cache_in_tx(
                conn,
                source_text,
                source_lang,
                target_text,
                target_lang,
                confidence=confidence,
                source=source,
                is_user_confirmed=is_user_confirmed,
            )

    @staticmethod
    def _find_matching_category_row_in_tx(
        conn: sqlite3.Connection,
        raw_name: str,
    ) -> sqlite3.Row | None:
        clean = " ".join((raw_name or "").strip().split())
        if not clean:
            return None
        nkey = normalize_category_key(clean)
        rows = conn.execute(
            "SELECT * FROM categories ORDER BY display_order ASC, id ASC;"
        ).fetchall()
        for r in rows:
            if (
                r["normalized_key"] == nkey
                or normalize_category_key(r["name_en"] or "") == nkey
                or normalize_category_key(r["name_ar"] or "") == nkey
            ):
                return r

        # Check exact canonical EN <-> AR glossary synonym against existing categories
        # (keeps distinct phrases such as "عصائر" -> "Juices" vs "عصائر طازجة" -> "Fresh Juices" separate)
        alt: str | None = None
        if detect_primary_script(clean) == "ar":
            alt = CATEGORY_GLOSSARY_AR_TO_EN.get(clean) or CATEGORY_GLOSSARY_AR_TO_EN.get(nkey)
        else:
            alt = CATEGORY_GLOSSARY_EN_TO_AR.get(clean.casefold())
        if alt:
            alt_key = normalize_category_key(alt)
            for r in rows:
                if (
                    r["normalized_key"] == alt_key
                    or normalize_category_key(r["name_en"] or "") == alt_key
                    or normalize_category_key(r["name_ar"] or "") == alt_key
                ):
                    return r
        return None

    def _resolve_or_create_category_in_tx(
        self,
        conn: sqlite3.Connection,
        name: str,
        *,
        name_en: str = "",
        name_ar: str = "",
        create_missing: bool = True,
        require_perm: bool = True,
        live_actor: UserAccount | None = None,
    ) -> CategoryRecord:
        clean = " ".join((name or "").strip().split())[:40]
        if not clean:
            raise ValueError(tr("backend.err.category_req"))
        nkey = normalize_category_key(clean)
        if nkey in {"all", normalize_category_key("كل القائمة")}:
            raise ValueError(tr("backend.err.category_reserved"))

        matched = self._find_matching_category_row_in_tx(conn, clean)
        if matched is not None:
            return _row_to_category(matched)

        if not create_missing:
            raise ValueError(tr("backend.err.category_not_found", name=clean))

        actor_checked = live_actor
        if require_perm:
            actor_checked = self._resolve_actor(conn, live_actor, Permission.MANAGE_CATEGORIES)

        explicit_en = " ".join((name_en or "").strip().split())[:40]
        explicit_ar = " ".join((name_ar or "").strip().split())[:40]
        if explicit_en or explicit_ar:
            if not explicit_en and detect_primary_script(clean) != "ar":
                explicit_en = clean
            if not explicit_ar and detect_primary_script(clean) == "ar":
                explicit_ar = clean
            if not explicit_en and explicit_ar:
                sug = suggest_bilingual_name(
                    explicit_ar,
                    is_category=True,
                    cache_lookup=self._make_tx_cache_lookup(conn),
                )
                explicit_en = sug.target_text
            if not explicit_ar and explicit_en:
                sug = suggest_bilingual_name(
                    explicit_en,
                    is_category=True,
                    cache_lookup=self._make_tx_cache_lookup(conn),
                )
                explicit_ar = sug.target_text
            final_en, final_ar = explicit_en, explicit_ar
        else:
            final_en, final_ar, _sug = suggest_bilingual_pair(
                clean,
                is_category=True,
                cache_lookup=self._make_tx_cache_lookup(conn),
            )

        canonical_key = normalize_category_key(final_en or final_ar or clean)
        dup_row = conn.execute(
            "SELECT * FROM categories WHERE normalized_key = ?;", (canonical_key,)
        ).fetchone()
        if dup_row is not None:
            return _row_to_category(dup_row)

        order_row = conn.execute(
            "SELECT COALESCE(MAX(display_order), 0) FROM categories WHERE display_order < 900;"
        ).fetchone()
        next_order = (int(order_row[0]) if order_row and order_row[0] is not None else 0) + 10
        now = _utc_now_iso()

        cur = conn.execute(
            """
            INSERT INTO categories (
                normalized_key, name_en, name_ar, display_order,
                is_system, is_archived, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0, 0, ?, ?);
            """,
            (canonical_key, final_en, final_ar, next_order, now, now),
        )
        cat_id = int(cur.lastrowid)
        if final_en and final_ar:
            is_conf = bool(explicit_en and explicit_ar)
            src_kind = "user_cache" if is_conf else "offline_dict"
            conf = "confirmed" if is_conf else "medium"
            self._upsert_translation_cache_in_tx(
                conn, final_en, "en", final_ar, "ar",
                confidence=conf, source=src_kind, is_user_confirmed=is_conf,
            )
            self._upsert_translation_cache_in_tx(
                conn, final_ar, "ar", final_en, "en",
                confidence=conf, source=src_kind, is_user_confirmed=is_conf,
            )

        if actor_checked is not None:
            self._write_audit(
                conn,
                "CATEGORY_CREATED",
                actor=actor_checked,
                target_type="category",
                target_id=str(cat_id),
                details=f"Created category '{final_en or final_ar}'",
            )
        created = conn.execute("SELECT * FROM categories WHERE id = ?;", (cat_id,)).fetchone()
        return _row_to_category(created)

    def list_category_records(self, *, include_archived: bool = False) -> list[CategoryRecord]:
        with self._tx() as conn:
            if include_archived:
                rows = conn.execute(
                    """
                    SELECT * FROM categories
                    ORDER BY is_archived ASC, display_order ASC, id ASC;
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM categories
                    WHERE is_archived = 0
                    ORDER BY display_order ASC, id ASC;
                    """
                ).fetchall()
            return [_row_to_category(r) for r in rows]

    def list_categories(self, *, include_archived: bool = False) -> list[str]:
        return [c.primary_name for c in self.list_category_records(include_archived=include_archived)]

    def get_category_product_counts(self) -> dict[int, int]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT category_id, COUNT(*) AS cnt
                FROM products
                WHERE category_id IS NOT NULL
                GROUP BY category_id;
                """
            ).fetchall()
            return {int(r["category_id"]): int(r["cnt"]) for r in rows}

    def get_category_counts(self, *, include_archived: bool = False) -> dict[str, int]:
        where_extra = "" if include_archived else "AND COALESCE(c.is_archived, 0) = 0"
        with self._tx() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    COALESCE(NULLIF(c.name_en, ''), c.name_ar, p.category) AS cat_name,
                    COUNT(p.id) AS cnt
                FROM products p
                LEFT JOIN categories c ON c.id = p.category_id
                WHERE p.category != '' {where_extra}
                GROUP BY COALESCE(c.id, p.category)
                ORDER BY COALESCE(c.display_order, 500) ASC, cat_name COLLATE NOCASE ASC;
                """
            ).fetchall()
            return {str(row["cat_name"]): int(row["cnt"]) for row in rows if int(row["cnt"]) > 0}

    def create_category(
        self,
        name: str,
        *,
        name_en: str = "",
        name_ar: str = "",
        actor: UserAccount | None = None,
    ) -> CategoryRecord:
        clean = " ".join((name or "").strip().split())[:40]
        if not clean:
            raise ValueError(tr("backend.err.category_req"))
        nkey = normalize_category_key(clean)
        if nkey in {"all", normalize_category_key("كل القائمة")}:
            raise ValueError(tr("backend.err.category_reserved"))

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_CATEGORIES)
            dup = self._find_matching_category_row_in_tx(conn, clean)
            if dup is None and name_en.strip():
                dup = self._find_matching_category_row_in_tx(conn, name_en)
            if dup is None and name_ar.strip():
                dup = self._find_matching_category_row_in_tx(conn, name_ar)
            if dup is not None:
                raise ValueError(tr("backend.err.category_exists", name=clean))

            return self._resolve_or_create_category_in_tx(
                conn,
                clean,
                name_en=name_en,
                name_ar=name_ar,
                create_missing=True,
                require_perm=False,
                live_actor=live_actor,
            )

    def rename_category(
        self,
        category_id: int,
        new_name: str,
        *,
        name_en: str | None = None,
        name_ar: str | None = None,
        actor: UserAccount | None = None,
    ) -> CategoryRecord:
        clean = " ".join((new_name or "").strip().split())[:40]
        if not clean:
            raise ValueError(tr("backend.err.category_req"))
        if normalize_category_key(clean) in {"all", normalize_category_key("كل القائمة")}:
            raise ValueError(tr("backend.err.category_reserved"))

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_CATEGORIES)
            row = conn.execute(
                "SELECT * FROM categories WHERE id = ?;", (int(category_id),)
            ).fetchone()
            if row is None:
                raise ValueError(tr("backend.err.category_not_found", name=str(category_id)))

            old_rec = _row_to_category(row)
            if name_en is None and name_ar is None:
                if detect_primary_script(clean) == "ar":
                    eff_ar = clean
                    eff_en = old_rec.name_en
                else:
                    eff_en = clean
                    eff_ar = old_rec.name_ar
            else:
                eff_en = " ".join((name_en or "").strip().split())[:40] if name_en is not None else old_rec.name_en
                eff_ar = " ".join((name_ar or "").strip().split())[:40] if name_ar is not None else old_rec.name_ar
                if not eff_en and detect_primary_script(clean) != "ar":
                    eff_en = clean
                if not eff_ar and detect_primary_script(clean) == "ar":
                    eff_ar = clean

            new_nkey = normalize_category_key(eff_en or eff_ar or clean)
            other_rows = conn.execute(
                "SELECT * FROM categories WHERE id != ?;", (int(category_id),)
            ).fetchall()
            check_keys = {k for k in (new_nkey, normalize_category_key(eff_en), normalize_category_key(eff_ar)) if k}
            for o in other_rows:
                o_keys = {
                    k
                    for k in (
                        o["normalized_key"],
                        normalize_category_key(o["name_en"] or ""),
                        normalize_category_key(o["name_ar"] or ""),
                    )
                    if k
                }
                if check_keys & o_keys:
                    raise ValueError(tr("backend.err.category_exists", name=clean))

            now = _utc_now_iso()
            conn.execute(
                """
                UPDATE categories
                SET normalized_key = ?, name_en = ?, name_ar = ?, updated_at = ?
                WHERE id = ?;
                """,
                (new_nkey, eff_en, eff_ar, now, int(category_id)),
            )
            primary_after = (eff_en or eff_ar).strip()
            conn.execute(
                "UPDATE products SET category = ?, updated_at = ? WHERE category_id = ?;",
                (primary_after, now, int(category_id)),
            )

            if eff_en and eff_ar:
                self._upsert_translation_cache_in_tx(
                    conn, eff_en, "en", eff_ar, "ar",
                    confidence="confirmed", source="user_cache", is_user_confirmed=True,
                )
                self._upsert_translation_cache_in_tx(
                    conn, eff_ar, "ar", eff_en, "en",
                    confidence="confirmed", source="user_cache", is_user_confirmed=True,
                )

            if live_actor is not None:
                self._write_audit(
                    conn,
                    "CATEGORY_RENAMED",
                    actor=live_actor,
                    target_type="category",
                    target_id=str(category_id),
                    details=f"Renamed category '{old_rec.primary_name}' -> '{primary_after}'",
                )
            updated = conn.execute(
                "SELECT * FROM categories WHERE id = ?;", (int(category_id),)
            ).fetchone()
            return _row_to_category(updated)

    def delete_category(
        self,
        category_id: int,
        *,
        move_to_category_id: int | None = None,
        archive_if_nonempty: bool = False,
        actor: UserAccount | None = None,
    ) -> str:
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_CATEGORIES)
            row = conn.execute(
                "SELECT * FROM categories WHERE id = ?;", (int(category_id),)
            ).fetchone()
            if row is None:
                raise ValueError(tr("backend.err.category_not_found", name=str(category_id)))
            cat_rec = _row_to_category(row)

            cnt_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM products WHERE category_id = ?;",
                (int(category_id),),
            ).fetchone()
            prod_count = int(cnt_row["cnt"]) if cnt_row else 0
            now = _utc_now_iso()

            if prod_count == 0:
                conn.execute("DELETE FROM categories WHERE id = ?;", (int(category_id),))
                if live_actor is not None:
                    self._write_audit(
                        conn,
                        "CATEGORY_DELETED",
                        actor=live_actor,
                        target_type="category",
                        target_id=str(category_id),
                        details=f"Deleted empty category '{cat_rec.primary_name}'",
                    )
                return "deleted"

            if archive_if_nonempty:
                conn.execute(
                    "UPDATE categories SET is_archived = 1, updated_at = ? WHERE id = ?;",
                    (now, int(category_id)),
                )
                if live_actor is not None:
                    self._write_audit(
                        conn,
                        "CATEGORY_ARCHIVED",
                        actor=live_actor,
                        target_type="category",
                        target_id=str(category_id),
                        details=f"Archived category '{cat_rec.primary_name}' ({prod_count} products)",
                    )
                return "archived"

            if move_to_category_id is not None:
                if int(move_to_category_id) == int(category_id):
                    raise ValueError(tr("backend.err.category_move_same"))
                target_row = conn.execute(
                    "SELECT * FROM categories WHERE id = ?;",
                    (int(move_to_category_id),),
                ).fetchone()
                if target_row is None or bool(target_row["is_archived"]):
                    raise ValueError(
                        tr("backend.err.category_not_found", name=str(move_to_category_id))
                    )
                target_rec = _row_to_category(target_row)
                conn.execute(
                    """
                    UPDATE products
                    SET category_id = ?, category = ?, updated_at = ?
                    WHERE category_id = ?;
                    """,
                    (target_rec.id, target_rec.primary_name, now, int(category_id)),
                )
                conn.execute("DELETE FROM categories WHERE id = ?;", (int(category_id),))
                if live_actor is not None:
                    self._write_audit(
                        conn,
                        "PRODUCTS_REASSIGNED",
                        actor=live_actor,
                        target_type="category",
                        target_id=str(target_rec.id),
                        details=f"Moved {prod_count} products from '{cat_rec.primary_name}' to '{target_rec.primary_name}'",
                    )
                    self._write_audit(
                        conn,
                        "CATEGORY_DELETED",
                        actor=live_actor,
                        target_type="category",
                        target_id=str(category_id),
                        details=f"Deleted category '{cat_rec.primary_name}' after reassigning products",
                    )
                return "moved_and_deleted"

            raise ValueError(tr("backend.err.category_in_use", count=prod_count))

    def archive_category(
        self, category_id: int, *, actor: UserAccount | None = None
    ) -> CategoryRecord:
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_CATEGORIES)
            row = conn.execute(
                "SELECT * FROM categories WHERE id = ?;", (int(category_id),)
            ).fetchone()
            if row is None:
                raise ValueError(tr("backend.err.category_not_found", name=str(category_id)))
            now = _utc_now_iso()
            conn.execute(
                "UPDATE categories SET is_archived = 1, updated_at = ? WHERE id = ?;",
                (now, int(category_id)),
            )
            updated = conn.execute(
                "SELECT * FROM categories WHERE id = ?;", (int(category_id),)
            ).fetchone()
            rec = _row_to_category(updated)
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "CATEGORY_ARCHIVED",
                    actor=live_actor,
                    target_type="category",
                    target_id=str(category_id),
                    details=f"Archived category '{rec.primary_name}'",
                )
            return rec

    def restore_category(
        self, category_id: int, *, actor: UserAccount | None = None
    ) -> CategoryRecord:
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_CATEGORIES)
            row = conn.execute(
                "SELECT * FROM categories WHERE id = ?;", (int(category_id),)
            ).fetchone()
            if row is None:
                raise ValueError(tr("backend.err.category_not_found", name=str(category_id)))
            now = _utc_now_iso()
            conn.execute(
                "UPDATE categories SET is_archived = 0, updated_at = ? WHERE id = ?;",
                (now, int(category_id)),
            )
            updated = conn.execute(
                "SELECT * FROM categories WHERE id = ?;", (int(category_id),)
            ).fetchone()
            rec = _row_to_category(updated)
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "CATEGORY_RESTORED",
                    actor=live_actor,
                    target_type="category",
                    target_id=str(category_id),
                    details=f"Restored category '{rec.primary_name}'",
                )
            return rec

    def get_order_count(self) -> int:
        with self._tx() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM orders;").fetchone()
            return int(row["cnt"]) if row else 0

    def list_products(
        self,
        search: str = "",
        category: str = "",
        *,
        include_archived_categories: bool = True,
    ) -> list[Product]:
        clauses: list[str] = []
        params: list[object] = []

        if not include_archived_categories:
            clauses.append("COALESCE(c.is_archived, 0) = 0")

        cat_clean = (category or "").strip()
        if cat_clean and cat_clean != "All":
            cat_norm = normalize_category_key(cat_clean)
            clauses.append(
                "(p.category = ? COLLATE NOCASE "
                "OR c.name_en = ? COLLATE NOCASE "
                "OR c.name_ar = ? COLLATE NOCASE "
                "OR c.normalized_key = ?)"
            )
            params.extend([cat_clean, cat_clean, cat_clean, cat_norm])

        q = " ".join((search or "").strip().split())
        if q:
            like = f"%{q}%"
            norm_like = f"%{normalize_category_key(q)}%"
            clauses.append(
                "(p.name LIKE ? OR p.name_en LIKE ? OR p.name_ar LIKE ? "
                "OR p.category LIKE ? OR c.name_en LIKE ? OR c.name_ar LIKE ? "
                "OR c.normalized_key LIKE ? OR p.sku LIKE ?)"
            )
            params.extend([like, like, like, like, like, like, norm_like, like])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT
                p.id, p.name, p.category, p.price_minor, p.created_at, p.updated_at,
                p.image_filename, p.image_source, p.image_attribution, p.image_license, p.image_source_url,
                p.name_en, p.name_ar, p.category_id, p.sku
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            {where_sql}
            ORDER BY COALESCE(c.display_order, 500) ASC, p.category COLLATE NOCASE ASC, p.name COLLATE NOCASE ASC;
        """
        with self._tx() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_product(r) for r in rows]

    def list_referenced_image_filenames(self) -> set[str]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT DISTINCT image_filename FROM products WHERE image_filename != '';"
            ).fetchall()
            return {row["image_filename"] for row in rows if row["image_filename"]}

    def add_product(
        self,
        name: str,
        category: str,
        price_minor: int,
        *,
        image_filename: str = "",
        image_source: str = "",
        image_attribution: str = "",
        image_license: str = "",
        image_source_url: str = "",
        name_en: str | None = None,
        name_ar: str | None = None,
        category_id: int | None = None,
        pending_category_en: str = "",
        pending_category_ar: str = "",
        sku: str | None = None,
        actor: UserAccount | None = None,
    ) -> Product:
        clean_name = " ".join((name or "").strip().split())[:80]
        clean_cat = " ".join((category or "").strip().split())[:40]
        if not clean_name:
            raise ValueError(tr("backend.err.product_name_req"))
        if not clean_cat and category_id is None:
            raise ValueError(tr("backend.err.category_req"))
        if not isinstance(price_minor, int) or price_minor < 0:
            raise ValueError(tr("backend.err.price_non_neg"))

        clean_img = (image_filename or "").strip()[:200]
        clean_src = (image_source or "").strip()[:40]
        clean_attr = (image_attribution or "").strip()[:300]
        clean_lic = (image_license or "").strip()[:120]
        clean_url = (image_source_url or "").strip()[:400]
        clean_sku = (sku or "").strip()[:64] if sku is not None else ""

        now = _utc_now_iso()
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_PRODUCTS)
            if category_id is not None:
                cat_row = conn.execute(
                    "SELECT * FROM categories WHERE id = ?;", (int(category_id),)
                ).fetchone()
                if cat_row is not None:
                    cat_rec = _row_to_category(cat_row)
                else:
                    cat_rec = self._resolve_or_create_category_in_tx(
                        conn,
                        clean_cat,
                        name_en=pending_category_en,
                        name_ar=pending_category_ar,
                        create_missing=True,
                        require_perm=True,
                        live_actor=live_actor,
                    )
            else:
                cat_rec = self._resolve_or_create_category_in_tx(
                    conn,
                    clean_cat,
                    name_en=pending_category_en,
                    name_ar=pending_category_ar,
                    create_missing=True,
                    require_perm=True,
                    live_actor=live_actor,
                )

            if name_en is None and name_ar is None:
                if clean_name in DEFAULT_PRODUCT_AR_NAMES:
                    eff_en = clean_name
                    eff_ar = DEFAULT_PRODUCT_AR_NAMES[clean_name]
                else:
                    eff_en, eff_ar, _sug = suggest_bilingual_pair(
                        clean_name,
                        is_category=False,
                        cache_lookup=self._make_tx_cache_lookup(conn),
                    )
            else:
                eff_en = " ".join((name_en or "").strip().split())[:80]
                eff_ar = " ".join((name_ar or "").strip().split())[:80]
                if not eff_en and detect_primary_script(clean_name) != "ar":
                    eff_en = clean_name
                if not eff_ar and detect_primary_script(clean_name) == "ar":
                    eff_ar = clean_name

            canonical_cat_name = cat_rec.primary_name
            cur = conn.execute(
                """
                INSERT INTO products (
                    name, category, price_minor, created_at, updated_at,
                    image_filename, image_source, image_attribution, image_license, image_source_url,
                    name_en, name_ar, category_id, sku
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    clean_name,
                    canonical_cat_name,
                    price_minor,
                    now,
                    now,
                    clean_img,
                    clean_src,
                    clean_attr,
                    clean_lic,
                    clean_url,
                    eff_en,
                    eff_ar,
                    cat_rec.id,
                    clean_sku,
                ),
            )
            product_id = int(cur.lastrowid)

            if eff_en and eff_ar and (name_en is not None or name_ar is not None):
                self._upsert_translation_cache_in_tx(
                    conn, eff_en, "en", eff_ar, "ar",
                    confidence="confirmed", source="user_cache", is_user_confirmed=True,
                )
                self._upsert_translation_cache_in_tx(
                    conn, eff_ar, "ar", eff_en, "en",
                    confidence="confirmed", source="user_cache", is_user_confirmed=True,
                )

            if live_actor is not None:
                self._write_audit(
                    conn,
                    "PRODUCT_CREATED",
                    actor=live_actor,
                    target_type="product",
                    target_id=str(product_id),
                    details=f"Added product '{clean_name}' ({canonical_cat_name})",
                )
            row = conn.execute("SELECT * FROM products WHERE id = ?;", (product_id,)).fetchone()
            return _row_to_product(row)

    def update_product(
        self,
        product_id: int,
        name: str,
        category: str,
        price_minor: int,
        *,
        image_filename: str | None = None,
        image_source: str | None = None,
        image_attribution: str | None = None,
        image_license: str | None = None,
        image_source_url: str | None = None,
        name_en: str | None = None,
        name_ar: str | None = None,
        category_id: int | None = None,
        pending_category_en: str = "",
        pending_category_ar: str = "",
        sku: str | None = None,
        actor: UserAccount | None = None,
    ) -> Product:
        clean_name = " ".join((name or "").strip().split())[:80]
        clean_cat = " ".join((category or "").strip().split())[:40]
        if not clean_name:
            raise ValueError(tr("backend.err.product_name_req"))
        if not clean_cat and category_id is None:
            raise ValueError(tr("backend.err.category_req"))
        if not isinstance(price_minor, int) or price_minor < 0:
            raise ValueError(tr("backend.err.price_non_neg"))

        now = _utc_now_iso()
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_PRODUCTS)
            existing = conn.execute(
                "SELECT * FROM products WHERE id = ?;", (int(product_id),)
            ).fetchone()
            if existing is None:
                raise ValueError(f"Product ID {product_id} was not found.")

            if category_id is not None:
                cat_row = conn.execute(
                    "SELECT * FROM categories WHERE id = ?;", (int(category_id),)
                ).fetchone()
                if cat_row is not None:
                    cat_rec = _row_to_category(cat_row)
                else:
                    cat_rec = self._resolve_or_create_category_in_tx(
                        conn,
                        clean_cat,
                        name_en=pending_category_en,
                        name_ar=pending_category_ar,
                        create_missing=True,
                        require_perm=True,
                        live_actor=live_actor,
                    )
            else:
                cat_rec = self._resolve_or_create_category_in_tx(
                    conn,
                    clean_cat,
                    name_en=pending_category_en,
                    name_ar=pending_category_ar,
                    create_missing=True,
                    require_perm=True,
                    live_actor=live_actor,
                )

            existing_en = (existing["name_en"] if "name_en" in existing.keys() else "") or ""
            existing_ar = (existing["name_ar"] if "name_ar" in existing.keys() else "") or ""
            if name_en is None and name_ar is None:
                # Preserve the opposite confirmed language column when editing one language field only
                if detect_primary_script(clean_name) == "ar":
                    eff_ar = clean_name
                    eff_en = existing_en
                else:
                    eff_en = clean_name
                    eff_ar = existing_ar
            else:
                eff_en = (
                    " ".join((name_en or "").strip().split())[:80]
                    if name_en is not None
                    else existing_en
                )
                eff_ar = (
                    " ".join((name_ar or "").strip().split())[:80]
                    if name_ar is not None
                    else existing_ar
                )
                if not eff_en and detect_primary_script(clean_name) != "ar":
                    eff_en = clean_name
                if not eff_ar and detect_primary_script(clean_name) == "ar":
                    eff_ar = clean_name

            img_fn = (
                (image_filename or "").strip()[:200]
                if image_filename is not None
                else (existing["image_filename"] or "")
            )
            img_src = (
                (image_source or "").strip()[:40]
                if image_source is not None
                else (existing["image_source"] or "")
            )
            img_attr = (
                (image_attribution or "").strip()[:300]
                if image_attribution is not None
                else (existing["image_attribution"] or "")
            )
            img_lic = (
                (image_license or "").strip()[:120]
                if image_license is not None
                else (existing["image_license"] or "")
            )
            img_url = (
                (image_source_url or "").strip()[:400]
                if image_source_url is not None
                else (existing["image_source_url"] or "")
            )
            clean_sku = (
                (sku or "").strip()[:64]
                if sku is not None
                else ((existing["sku"] if "sku" in existing.keys() else "") or "")
            )

            canonical_cat_name = cat_rec.primary_name
            conn.execute(
                """
                UPDATE products
                SET name = ?, category = ?, price_minor = ?, updated_at = ?,
                    image_filename = ?, image_source = ?, image_attribution = ?,
                    image_license = ?, image_source_url = ?,
                    name_en = ?, name_ar = ?, category_id = ?, sku = ?
                WHERE id = ?;
                """,
                (
                    clean_name,
                    canonical_cat_name,
                    price_minor,
                    now,
                    img_fn,
                    img_src,
                    img_attr,
                    img_lic,
                    img_url,
                    eff_en,
                    eff_ar,
                    cat_rec.id,
                    clean_sku,
                    int(product_id),
                ),
            )
            if eff_en and eff_ar and (name_en is not None or name_ar is not None):
                self._upsert_translation_cache_in_tx(
                    conn, eff_en, "en", eff_ar, "ar",
                    confidence="confirmed", source="user_cache", is_user_confirmed=True,
                )
                self._upsert_translation_cache_in_tx(
                    conn, eff_ar, "ar", eff_en, "en",
                    confidence="confirmed", source="user_cache", is_user_confirmed=True,
                )
            row = conn.execute(
                "SELECT * FROM products WHERE id = ?;", (int(product_id),)
            ).fetchone()
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "PRODUCT_UPDATED",
                    actor=live_actor,
                    target_type="product",
                    target_id=str(product_id),
                    details=f"Updated product '{clean_name}' ({canonical_cat_name})",
                )
        return _row_to_product(row)

    def delete_product(self, product_id: int, *, actor: UserAccount | None = None) -> None:
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_PRODUCTS)
            existing = conn.execute(
                "SELECT name FROM products WHERE id = ?;", (int(product_id),)
            ).fetchone()
            cur = conn.execute("DELETE FROM products WHERE id = ?;", (int(product_id),))
            if cur.rowcount == 0:
                raise ValueError(f"Product ID {product_id} was not found.")
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "PRODUCT_DELETED",
                    actor=live_actor,
                    target_type="product",
                    target_id=str(product_id),
                    details=f"Deleted product '{existing['name'] if existing else product_id}'",
                )

    def restore_sample_products(self, *, actor: UserAccount | None = None) -> None:
        now = _utc_now_iso()
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_PRODUCTS)
            for cat_en, cat_ar, order_val, is_sys in DEFAULT_SYSTEM_CATEGORIES:
                nkey = normalize_category_key(cat_en)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO categories (
                        normalized_key, name_en, name_ar, display_order,
                        is_system, is_archived, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?);
                    """,
                    (nkey, cat_en, cat_ar, order_val, is_sys, now, now),
                )
                conn.execute(
                    "UPDATE categories SET is_archived = 0, updated_at = ? WHERE normalized_key = ?;",
                    (now, nkey),
                )
            cat_rows = conn.execute("SELECT id, name_en FROM categories;").fetchall()
            cat_id_by_key = {
                normalize_category_key(r["name_en"]): int(r["id"]) for r in cat_rows
            }

            conn.execute("DELETE FROM products;")
            conn.executemany(
                """
                INSERT INTO products (
                    name, category, price_minor, created_at, updated_at,
                    name_en, name_ar, category_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                [
                    (
                        name,
                        cat,
                        price,
                        now,
                        now,
                        name,
                        DEFAULT_PRODUCT_AR_NAMES.get(name, ""),
                        cat_id_by_key.get(normalize_category_key(cat)),
                    )
                    for name, cat, price in DEFAULT_PRODUCTS
                ],
            )
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "PRODUCTS_RESTORED",
                    actor=live_actor,
                    target_type="product",
                    details="Restored 12 default café sample products",
                )

    # ---------------- Product Modifiers & Per-Product Overrides ----------------

    @staticmethod
    def _load_modifier_groups_in_tx(
        conn: sqlite3.Connection,
        *,
        include_inactive: bool = True,
        include_archived: bool = False,
        product_id: int | None = None,
    ) -> list[ModifierGroup]:
        clauses: list[str] = []
        params: list[object] = []
        if not include_archived:
            clauses.append("g.is_archived = 0")
        if not include_inactive:
            clauses.append("g.is_active = 1")

        if product_id is not None:
            where_extra = f"AND {' AND '.join(clauses)}" if clauses else ""
            group_rows = conn.execute(
                f"""
                SELECT g.*
                FROM modifier_groups g
                JOIN product_modifier_groups pmg ON pmg.group_id = g.id
                WHERE pmg.product_id = ? {where_extra}
                ORDER BY pmg.display_order ASC, g.display_order ASC, g.id ASC;
                """,
                (int(product_id),),
            ).fetchall()
        else:
            where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            group_rows = conn.execute(
                f"""
                SELECT g.*
                FROM modifier_groups g
                {where_sql}
                ORDER BY g.is_archived ASC, g.display_order ASC, g.id ASC;
                """,
                params,
            ).fetchall()

        if not group_rows:
            return []

        g_ids = [int(r["id"]) for r in group_rows]
        g_ph = ",".join("?" for _ in g_ids)

        opt_clauses = [f"o.group_id IN ({g_ph})"]
        if not include_archived:
            opt_clauses.append("o.is_archived = 0")
        if not include_inactive:
            opt_clauses.append("o.is_active = 1")
        opt_rows = conn.execute(
            f"""
            SELECT o.*
            FROM modifier_options o
            WHERE {' AND '.join(opt_clauses)}
            ORDER BY o.display_order ASC, o.id ASC;
            """,
            g_ids,
        ).fetchall()

        overrides: dict[int, int] = {}
        if product_id is not None:
            ov_rows = conn.execute(
                """
                SELECT option_id, price_delta_minor
                FROM product_modifier_option_overrides
                WHERE product_id = ?;
                """,
                (int(product_id),),
            ).fetchall()
            overrides = {int(r["option_id"]): int(r["price_delta_minor"]) for r in ov_rows}

        opts_by_group: dict[int, list[ModifierOption]] = {}
        for orow in opt_rows:
            oid = int(orow["id"])
            gid = int(orow["group_id"])
            base_delta = int(orow["price_delta_minor"])
            has_ov = oid in overrides
            eff_delta = overrides[oid] if has_ov else base_delta
            opts_by_group.setdefault(gid, []).append(
                ModifierOption(
                    id=oid,
                    group_id=gid,
                    name_en=(orow["name_en"] or "").strip(),
                    name_ar=(orow["name_ar"] or "").strip(),
                    price_delta_minor=base_delta,
                    is_active=bool(orow["is_active"]),
                    is_archived=bool(orow["is_archived"]),
                    display_order=int(orow["display_order"]),
                    effective_price_delta_minor=eff_delta,
                    has_price_override=has_ov,
                )
            )

        pmg_rows = conn.execute(
            f"""
            SELECT group_id, product_id
            FROM product_modifier_groups
            WHERE group_id IN ({g_ph})
            ORDER BY product_id ASC;
            """,
            g_ids,
        ).fetchall()
        prods_by_group: dict[int, list[int]] = {}
        for pr in pmg_rows:
            prods_by_group.setdefault(int(pr["group_id"]), []).append(int(pr["product_id"]))

        out: list[ModifierGroup] = []
        for gr in group_rows:
            gid = int(gr["id"])
            out.append(
                ModifierGroup(
                    id=gid,
                    name_en=(gr["name_en"] or "").strip(),
                    name_ar=(gr["name_ar"] or "").strip(),
                    selection_mode=(gr["selection_mode"] or MODIFIER_MODE_SINGLE),
                    is_required=bool(gr["is_required"]),
                    min_select=int(gr["min_select"]),
                    max_select=int(gr["max_select"]),
                    is_active=bool(gr["is_active"]),
                    is_archived=bool(gr["is_archived"]),
                    display_order=int(gr["display_order"]),
                    options=tuple(opts_by_group.get(gid, ())),
                    assigned_product_ids=tuple(prods_by_group.get(gid, ())),
                )
            )
        return out

    def list_modifier_groups(
        self,
        *,
        include_inactive: bool = True,
        include_archived: bool = False,
    ) -> list[ModifierGroup]:
        with self._tx() as conn:
            return self._load_modifier_groups_in_tx(
                conn,
                include_inactive=include_inactive,
                include_archived=include_archived,
            )

    def get_modifier_group(self, group_id: int) -> ModifierGroup | None:
        with self._tx() as conn:
            groups = self._load_modifier_groups_in_tx(
                conn, include_inactive=True, include_archived=True
            )
            for g in groups:
                if g.id == int(group_id):
                    return g
            return None

    def get_product_modifiers(
        self, product_id: int, *, include_inactive: bool = False
    ) -> list[ModifierGroup]:
        with self._tx() as conn:
            return self._load_modifier_groups_in_tx(
                conn,
                include_inactive=include_inactive,
                include_archived=False,
                product_id=int(product_id),
            )

    def list_product_modifier_counts(self) -> dict[int, int]:
        """Return count of active non-archived modifier groups assigned to each product."""
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT pmg.product_id, COUNT(DISTINCT g.id) AS cnt
                FROM product_modifier_groups pmg
                JOIN modifier_groups g ON g.id = pmg.group_id
                WHERE g.is_active = 1 AND g.is_archived = 0
                  AND EXISTS (
                      SELECT 1 FROM modifier_options o
                      WHERE o.group_id = g.id AND o.is_active = 1 AND o.is_archived = 0
                  )
                GROUP BY pmg.product_id;
                """
            ).fetchall()
            return {int(r["product_id"]): int(r["cnt"]) for r in rows}

    @staticmethod
    def _normalize_modifier_payload(
        name_en: str,
        name_ar: str,
        selection_mode: str,
        is_required: bool,
        min_select: int,
        max_select: int,
        options: list[dict[str, object]],
    ) -> tuple[str, str, str, int, int, int, list[dict[str, object]]]:
        clean_en = " ".join((name_en or "").strip().split())[:60]
        clean_ar = " ".join((name_ar or "").strip().split())[:60]
        if not clean_en and not clean_ar:
            raise ValueError(tr("backend.err.modifier_name_req"))
        if not clean_en and detect_primary_script(clean_ar) != "ar":
            clean_en = clean_ar

        mode = (selection_mode or MODIFIER_MODE_SINGLE).strip().lower()
        if mode not in (MODIFIER_MODE_SINGLE, MODIFIER_MODE_MULTI):
            mode = MODIFIER_MODE_SINGLE

        req = 1 if bool(is_required) else 0
        min_sel = max(0, int(min_select))
        max_sel = max(0, int(max_select))
        if mode == MODIFIER_MODE_SINGLE:
            max_sel = 1
            min_sel = 1 if req else (1 if min_sel > 0 else 0)
        else:
            if req and min_sel < 1:
                min_sel = 1
            if max_sel > 0 and min_sel > max_sel:
                raise ValueError(tr("modifiers.err_minmax"))

        cleaned_opts: list[dict[str, object]] = []
        active_opt_count = 0
        for idx, opt in enumerate(options or []):
            o_en = " ".join(str(opt.get("name_en", "") or "").strip().split())[:60]
            o_ar = " ".join(str(opt.get("name_ar", "") or "").strip().split())[:60]
            if not o_en and not o_ar:
                continue
            delta = int(opt.get("price_delta_minor", 0) or 0)
            if delta < 0:
                raise ValueError(tr("backend.err.modifier_delta_neg"))
            o_active = bool(opt.get("is_active", True))
            if o_active:
                active_opt_count += 1
            raw_oid = opt.get("id")
            opt_id = int(raw_oid) if raw_oid is not None else None
            cleaned_opts.append(
                {
                    "id": opt_id,
                    "name_en": o_en,
                    "name_ar": o_ar,
                    "price_delta_minor": delta,
                    "is_active": 1 if o_active else 0,
                    "display_order": int(opt.get("display_order", idx * 10)),
                }
            )
        if not cleaned_opts:
            raise ValueError(tr("modifiers.err_need_options"))
        if req and active_opt_count < max(1, min_sel):
            raise ValueError(tr("modifiers.err_required_min"))
        return clean_en, clean_ar, mode, req, min_sel, max_sel, cleaned_opts

    def create_modifier_group(
        self,
        name_en: str,
        *,
        name_ar: str = "",
        selection_mode: str = MODIFIER_MODE_SINGLE,
        is_required: bool = False,
        min_select: int = 0,
        max_select: int = 1,
        is_active: bool = True,
        options: list[dict[str, object]] | None = None,
        product_ids: list[int] | None = None,
        actor: UserAccount | None = None,
    ) -> ModifierGroup:
        clean_en, clean_ar, mode, req, min_sel, max_sel, cleaned_opts = (
            self._normalize_modifier_payload(
                name_en, name_ar, selection_mode, is_required, min_select, max_select, options or []
            )
        )
        now = _utc_now_iso()
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_PRODUCTS)
            max_ord_row = conn.execute(
                "SELECT COALESCE(MAX(display_order), 0) FROM modifier_groups;"
            ).fetchone()
            next_ord = int(max_ord_row[0] or 0) + 10
            cur = conn.execute(
                """
                INSERT INTO modifier_groups (
                    name_en, name_ar, selection_mode, is_required, min_select, max_select,
                    is_active, is_archived, display_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?);
                """,
                (
                    clean_en,
                    clean_ar,
                    mode,
                    req,
                    min_sel,
                    max_sel,
                    1 if is_active else 0,
                    next_ord,
                    now,
                    now,
                ),
            )
            group_id = int(cur.lastrowid)
            for idx, o in enumerate(cleaned_opts):
                conn.execute(
                    """
                    INSERT INTO modifier_options (
                        group_id, name_en, name_ar, price_delta_minor,
                        is_active, is_archived, display_order, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?);
                    """,
                    (
                        group_id,
                        o["name_en"],
                        o["name_ar"],
                        o["price_delta_minor"],
                        o["is_active"],
                        (idx + 1) * 10,
                        now,
                        now,
                    ),
                )
            if product_ids:
                valid_pids = {
                    int(r["id"]) for r in conn.execute("SELECT id FROM products;").fetchall()
                }
                for ord_idx, pid in enumerate(dict.fromkeys(int(p) for p in product_ids)):
                    if pid in valid_pids:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO product_modifier_groups (
                                product_id, group_id, display_order
                            ) VALUES (?, ?, ?);
                            """,
                            (pid, group_id, (ord_idx + 1) * 10),
                        )
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "MODIFIER_GROUP_CREATED",
                    actor=live_actor,
                    target_type="modifier_group",
                    target_id=str(group_id),
                    details=f"Created modifier group '{clean_en or clean_ar}' ({len(cleaned_opts)} options)",
                )
            groups = self._load_modifier_groups_in_tx(
                conn, include_inactive=True, include_archived=True
            )
            for g in groups:
                if g.id == group_id:
                    return g
            raise RuntimeError("Created modifier group could not be reloaded.")

    def update_modifier_group(
        self,
        group_id: int,
        name_en: str,
        *,
        name_ar: str = "",
        selection_mode: str = MODIFIER_MODE_SINGLE,
        is_required: bool = False,
        min_select: int = 0,
        max_select: int = 1,
        is_active: bool = True,
        options: list[dict[str, object]] | None = None,
        product_ids: list[int] | None = None,
        actor: UserAccount | None = None,
    ) -> ModifierGroup:
        clean_en, clean_ar, mode, req, min_sel, max_sel, cleaned_opts = (
            self._normalize_modifier_payload(
                name_en, name_ar, selection_mode, is_required, min_select, max_select, options or []
            )
        )
        now = _utc_now_iso()
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_PRODUCTS)
            existing = conn.execute(
                "SELECT id FROM modifier_groups WHERE id = ?;", (int(group_id),)
            ).fetchone()
            if existing is None:
                raise ValueError(tr("backend.err.modifier_group_not_found"))

            conn.execute(
                """
                UPDATE modifier_groups
                SET name_en = ?, name_ar = ?, selection_mode = ?,
                    is_required = ?, min_select = ?, max_select = ?,
                    is_active = ?, updated_at = ?
                WHERE id = ?;
                """,
                (
                    clean_en,
                    clean_ar,
                    mode,
                    req,
                    min_sel,
                    max_sel,
                    1 if is_active else 0,
                    now,
                    int(group_id),
                ),
            )

            existing_opt_ids = {
                int(r["id"])
                for r in conn.execute(
                    "SELECT id FROM modifier_options WHERE group_id = ?;", (int(group_id),)
                ).fetchall()
            }
            kept_opt_ids: set[int] = set()
            for idx, o in enumerate(cleaned_opts):
                oid = o["id"]
                disp_ord = (idx + 1) * 10
                if oid is not None and oid in existing_opt_ids:
                    conn.execute(
                        """
                        UPDATE modifier_options
                        SET name_en = ?, name_ar = ?, price_delta_minor = ?,
                            is_active = ?, is_archived = 0, display_order = ?, updated_at = ?
                        WHERE id = ? AND group_id = ?;
                        """,
                        (
                            o["name_en"],
                            o["name_ar"],
                            o["price_delta_minor"],
                            o["is_active"],
                            disp_ord,
                            now,
                            oid,
                            int(group_id),
                        ),
                    )
                    kept_opt_ids.add(oid)
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO modifier_options (
                            group_id, name_en, name_ar, price_delta_minor,
                            is_active, is_archived, display_order, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?);
                        """,
                        (
                            int(group_id),
                            o["name_en"],
                            o["name_ar"],
                            o["price_delta_minor"],
                            o["is_active"],
                            disp_ord,
                            now,
                            now,
                        ),
                    )
                    kept_opt_ids.add(int(cur.lastrowid))

            removed_opt_ids = existing_opt_ids - kept_opt_ids
            for rem_id in removed_opt_ids:
                conn.execute(
                    "DELETE FROM product_modifier_option_overrides WHERE option_id = ?;",
                    (rem_id,),
                )
                conn.execute(
                    """
                    UPDATE modifier_options
                    SET is_archived = 1, is_active = 0, updated_at = ?
                    WHERE id = ?;
                    """,
                    (now, rem_id),
                )

            if product_ids is not None:
                valid_pids = {
                    int(r["id"]) for r in conn.execute("SELECT id FROM products;").fetchall()
                }
                conn.execute(
                    "DELETE FROM product_modifier_groups WHERE group_id = ?;",
                    (int(group_id),),
                )
                for ord_idx, pid in enumerate(dict.fromkeys(int(p) for p in product_ids)):
                    if pid in valid_pids:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO product_modifier_groups (
                                product_id, group_id, display_order
                            ) VALUES (?, ?, ?);
                            """,
                            (pid, int(group_id), (ord_idx + 1) * 10),
                        )

            if live_actor is not None:
                self._write_audit(
                    conn,
                    "MODIFIER_GROUP_UPDATED",
                    actor=live_actor,
                    target_type="modifier_group",
                    target_id=str(group_id),
                    details=f"Updated modifier group '{clean_en or clean_ar}' ({len(cleaned_opts)} options)",
                )

            groups = self._load_modifier_groups_in_tx(
                conn, include_inactive=True, include_archived=True
            )
            for g in groups:
                if g.id == int(group_id):
                    return g
            raise RuntimeError("Updated modifier group could not be reloaded.")

    def archive_modifier_group(
        self,
        group_id: int,
        *,
        archived: bool = True,
        actor: UserAccount | None = None,
    ) -> ModifierGroup:
        now = _utc_now_iso()
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_PRODUCTS)
            row = conn.execute(
                "SELECT id, name_en, name_ar FROM modifier_groups WHERE id = ?;",
                (int(group_id),),
            ).fetchone()
            if row is None:
                raise ValueError(tr("backend.err.modifier_group_not_found"))
            arch_val = 1 if archived else 0
            conn.execute(
                "UPDATE modifier_groups SET is_archived = ?, updated_at = ? WHERE id = ?;",
                (arch_val, now, int(group_id)),
            )
            if live_actor is not None:
                action = "MODIFIER_GROUP_ARCHIVED" if archived else "MODIFIER_GROUP_RESTORED"
                lbl = (row["name_en"] or row["name_ar"] or str(group_id)).strip()
                self._write_audit(
                    conn,
                    action,
                    actor=live_actor,
                    target_type="modifier_group",
                    target_id=str(group_id),
                    details=f"{'Archived' if archived else 'Restored'} modifier group '{lbl}'",
                )
            groups = self._load_modifier_groups_in_tx(
                conn, include_inactive=True, include_archived=True
            )
            for g in groups:
                if g.id == int(group_id):
                    return g
            raise RuntimeError("Modifier group could not be reloaded.")

    def set_product_modifier_config(
        self,
        product_id: int,
        group_ids: list[int],
        option_price_overrides: dict[int, int] | None = None,
        *,
        actor: UserAccount | None = None,
    ) -> list[ModifierGroup]:
        overrides_in = option_price_overrides or {}
        for delta in overrides_in.values():
            if not isinstance(delta, int) or delta < 0:
                raise ValueError(tr("backend.err.modifier_delta_neg"))

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_PRODUCTS)
            prod = conn.execute(
                "SELECT id, name FROM products WHERE id = ?;", (int(product_id),)
            ).fetchone()
            if prod is None:
                raise ValueError(f"Product ID {product_id} was not found.")

            clean_gids = list(dict.fromkeys(int(g) for g in group_ids))
            if clean_gids:
                g_ph = ",".join("?" for _ in clean_gids)
                valid_gids = {
                    int(r["id"])
                    for r in conn.execute(
                        f"SELECT id FROM modifier_groups WHERE id IN ({g_ph}) AND is_archived = 0;",
                        clean_gids,
                    ).fetchall()
                }
                clean_gids = [g for g in clean_gids if g in valid_gids]

            conn.execute(
                "DELETE FROM product_modifier_groups WHERE product_id = ?;",
                (int(product_id),),
            )
            for idx, gid in enumerate(clean_gids):
                conn.execute(
                    """
                    INSERT INTO product_modifier_groups (product_id, group_id, display_order)
                    VALUES (?, ?, ?);
                    """,
                    (int(product_id), gid, (idx + 1) * 10),
                )

            conn.execute(
                "DELETE FROM product_modifier_option_overrides WHERE product_id = ?;",
                (int(product_id),),
            )
            if clean_gids and overrides_in:
                g_ph = ",".join("?" for _ in clean_gids)
                valid_opt_ids = {
                    int(r["id"])
                    for r in conn.execute(
                        f"""
                        SELECT id FROM modifier_options
                        WHERE group_id IN ({g_ph}) AND is_archived = 0;
                        """,
                        clean_gids,
                    ).fetchall()
                }
                for opt_id, delta_minor in overrides_in.items():
                    oid = int(opt_id)
                    if oid in valid_opt_ids:
                        conn.execute(
                            """
                            INSERT INTO product_modifier_option_overrides (
                                product_id, option_id, price_delta_minor
                            ) VALUES (?, ?, ?);
                            """,
                            (int(product_id), oid, int(delta_minor)),
                        )

            if live_actor is not None:
                self._write_audit(
                    conn,
                    "PRODUCT_MODIFIERS_UPDATED",
                    actor=live_actor,
                    target_type="product",
                    target_id=str(product_id),
                    details=(
                        f"Updated modifiers on '{prod['name']}' "
                        f"({len(clean_gids)} groups, {len(overrides_in)} price overrides)"
                    ),
                )
            return self._load_modifier_groups_in_tx(
                conn,
                include_inactive=False,
                include_archived=False,
                product_id=int(product_id),
            )

    def _validate_and_resolve_cart_lines_in_tx(
        self,
        conn: sqlite3.Connection,
        lines: list[CartLine],
    ) -> list[CartLine]:
        resolved: list[CartLine] = []
        for line in lines:
            if line.quantity <= 0:
                raise ValueError(tr("backend.err.qty_positive", name=line.name))
            if line.unit_price_minor < 0:
                raise ValueError(tr("backend.err.price_item_neg", name=line.name))

            prod_row = (
                conn.execute(
                    "SELECT id, price_minor, name, name_en, name_ar, category, image_filename FROM products WHERE id = ?;",
                    (int(line.product_id),),
                ).fetchone()
                if line.product_id
                else None
            )

            active_groups: list[ModifierGroup] = []
            if prod_row is not None:
                active_groups = self._load_modifier_groups_in_tx(
                    conn,
                    include_inactive=False,
                    include_archived=False,
                    product_id=int(prod_row["id"]),
                )

            opt_lookup: dict[int, tuple[ModifierGroup, ModifierOption]] = {}
            for grp in active_groups:
                for opt in grp.active_options:
                    opt_lookup[opt.id] = (grp, opt)

            chosen_mods: list[CartModifierSelection] = []
            chosen_by_group: dict[int, list[int]] = {}
            seen_opt_ids: set[int] = set()

            for mod in line.modifiers:
                if mod.option_id is None or int(mod.option_id) not in opt_lookup:
                    raise ValueError(
                        tr(
                            "backend.err.modifier_option_not_found",
                            name=mod.display_option_name() or mod.option_name or "Modifier",
                        )
                    )
                oid = int(mod.option_id)
                if oid in seen_opt_ids:
                    continue
                seen_opt_ids.add(oid)
                grp, opt = opt_lookup[oid]
                delta = opt.resolved_price_delta_minor
                chosen_by_group.setdefault(grp.id, []).append(oid)
                chosen_mods.append(
                    CartModifierSelection(
                        group_id=grp.id,
                        option_id=opt.id,
                        group_name=grp.name_en or grp.name_ar,
                        group_name_en=grp.name_en,
                        group_name_ar=grp.name_ar,
                        option_name=opt.name_en or opt.name_ar,
                        option_name_en=opt.name_en,
                        option_name_ar=opt.name_ar,
                        price_delta_minor=delta,
                    )
                )

            item_label = line.display_name() or line.name
            for grp in active_groups:
                avail_opts = grp.active_options
                if not avail_opts:
                    continue
                sel_count = len(chosen_by_group.get(grp.id, ()))
                gname = grp.display_name() or grp.name_en or grp.name_ar
                if grp.selection_mode == MODIFIER_MODE_SINGLE and sel_count > 1:
                    raise ValueError(
                        tr(
                            "backend.err.modifier_rule_violation",
                            product=item_label,
                            detail=tr("backend.err.modifier_single_only", group=gname),
                        )
                    )
                min_req = max(1, grp.min_select) if grp.is_required else grp.min_select
                if grp.is_required and sel_count < min_req:
                    raise ValueError(
                        tr(
                            "backend.err.modifier_rule_violation",
                            product=item_label,
                            detail=tr("backend.err.modifier_required_missing", group=gname, min=min_req),
                        )
                    )
                if (not grp.is_required) and grp.min_select > 0 and 0 < sel_count < grp.min_select:
                    raise ValueError(
                        tr(
                            "backend.err.modifier_rule_violation",
                            product=item_label,
                            detail=tr("backend.err.modifier_required_missing", group=gname, min=grp.min_select),
                        )
                    )
                if grp.max_select > 0 and sel_count > grp.max_select:
                    raise ValueError(
                        tr(
                            "backend.err.modifier_rule_violation",
                            product=item_label,
                            detail=tr("backend.err.modifier_too_many", group=gname, max=grp.max_select),
                        )
                    )

            if chosen_mods or line.base_unit_price_minor is not None:
                base_minor = (
                    int(prod_row["price_minor"])
                    if prod_row is not None
                    else line.resolved_base_unit_price_minor
                )
                mods_sum = sum(m.price_delta_minor for m in chosen_mods)
                final_unit_minor = max(0, base_minor + mods_sum)
            else:
                base_minor = int(line.unit_price_minor)
                final_unit_minor = int(line.unit_price_minor)

            resolved.append(
                CartLine(
                    product_id=line.product_id,
                    name=line.name,
                    unit_price_minor=final_unit_minor,
                    quantity=int(line.quantity),
                    image_filename=line.image_filename,
                    category=line.category,
                    name_en=line.name_en,
                    name_ar=line.name_ar,
                    modifiers=tuple(chosen_mods),
                    base_unit_price_minor=base_minor,
                )
            )
        return resolved

    # ---------------- Held Orders (Separate from Completed Sales) ----------------

    @staticmethod
    def _deserialize_modifiers_json(raw_json: str) -> tuple[CartModifierSelection, ...]:
        if not raw_json:
            return ()
        try:
            arr = json.loads(raw_json)
        except (ValueError, TypeError):
            return ()
        if not isinstance(arr, list):
            return ()
        out: list[CartModifierSelection] = []
        for entry in arr:
            if not isinstance(entry, dict):
                continue
            out.append(
                CartModifierSelection(
                    group_id=(
                        int(entry["group_id"])
                        if entry.get("group_id") is not None
                        else None
                    ),
                    option_id=(
                        int(entry["option_id"])
                        if entry.get("option_id") is not None
                        else None
                    ),
                    group_name=str(entry.get("group_name") or ""),
                    group_name_en=str(entry.get("group_name_en") or ""),
                    group_name_ar=str(entry.get("group_name_ar") or ""),
                    option_name=str(entry.get("option_name") or ""),
                    option_name_en=str(entry.get("option_name_en") or ""),
                    option_name_ar=str(entry.get("option_name_ar") or ""),
                    price_delta_minor=max(0, int(entry.get("price_delta_minor") or 0)),
                )
            )
        return tuple(out)

    @staticmethod
    def _serialize_modifiers_json(modifiers: tuple[CartModifierSelection, ...] | list[CartModifierSelection]) -> str:
        payload = [
            {
                "group_id": m.group_id,
                "option_id": m.option_id,
                "group_name": m.group_name,
                "group_name_en": m.group_name_en,
                "group_name_ar": m.group_name_ar,
                "option_name": m.option_name,
                "option_name_en": m.option_name_en,
                "option_name_ar": m.option_name_ar,
                "price_delta_minor": int(m.price_delta_minor),
            }
            for m in modifiers
        ]
        return json.dumps(payload, ensure_ascii=False)

    def _load_held_orders_in_tx(
        self,
        conn: sqlite3.Connection,
        *,
        held_order_id: int | None = None,
        status: str | None = HELD_STATUS_HELD,
        search: str = "",
    ) -> list[HeldOrderRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if held_order_id is not None:
            clauses.append("h.id = ?")
            params.append(int(held_order_id))
        if status:
            clauses.append("h.status = ?")
            params.append(status)
        q = (search or "").strip()
        if q:
            like = f"%{q}%"
            clauses.append(
                """
                (h.hold_number LIKE ?
                 OR h.table_number LIKE ?
                 OR h.customer_name LIKE ?
                 OR h.customer_phone LIKE ?
                 OR h.cashier_name_snapshot LIKE ?
                 OR EXISTS (
                     SELECT 1 FROM held_order_items hi2
                     WHERE hi2.held_order_id = h.id
                       AND (hi2.product_name LIKE ? OR hi2.product_name_en LIKE ? OR hi2.product_name_ar LIKE ?)
                 ))
                """
            )
            params.extend([like] * 8)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            SELECT * FROM held_orders h
            {where_sql}
            ORDER BY h.updated_at DESC, h.id DESC;
            """,
            params,
        ).fetchall()
        if not rows:
            return []

        h_ids = [int(r["id"]) for r in rows]
        h_ph = ",".join("?" for _ in h_ids)
        item_rows = conn.execute(
            f"""
            SELECT * FROM held_order_items
            WHERE held_order_id IN ({h_ph})
            ORDER BY id ASC;
            """,
            h_ids,
        ).fetchall()
        items_by_held: dict[int, list[HeldOrderItemRecord]] = {}
        for ir in item_rows:
            hid = int(ir["held_order_id"])
            items_by_held.setdefault(hid, []).append(
                HeldOrderItemRecord(
                    id=int(ir["id"]),
                    held_order_id=hid,
                    product_id=int(ir["product_id"]) if ir["product_id"] is not None else None,
                    product_name=ir["product_name"],
                    product_name_en=ir["product_name_en"] or "",
                    product_name_ar=ir["product_name_ar"] or "",
                    category_snapshot=ir["category_snapshot"] or "",
                    image_filename=ir["image_filename"] or "",
                    base_unit_price_minor=int(ir["base_unit_price_minor"]),
                    unit_price_minor=int(ir["unit_price_minor"]),
                    quantity=int(ir["quantity"]),
                    modifiers=self._deserialize_modifiers_json(ir["modifiers_json"] or ""),
                )
            )

        out: list[HeldOrderRecord] = []
        for r in rows:
            hid = int(r["id"])
            out.append(
                HeldOrderRecord(
                    id=hid,
                    hold_number=r["hold_number"],
                    status=r["status"],
                    order_type=r["order_type"] or ORDER_TYPE_DINE_IN,
                    table_number=r["table_number"] or "",
                    customer_name=r["customer_name"] or "",
                    customer_phone=r["customer_phone"] or "",
                    delivery_address=r["delivery_address"] or "",
                    order_notes=r["order_notes"] or "",
                    currency=r["currency"],
                    subtotal_minor=int(r["subtotal_minor"]),
                    item_count=int(r["item_count"]),
                    cashier_user_id=(
                        int(r["cashier_user_id"]) if r["cashier_user_id"] is not None else None
                    ),
                    cashier_name_snapshot=r["cashier_name_snapshot"] or "",
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    completed_order_id=(
                        int(r["completed_order_id"])
                        if r["completed_order_id"] is not None
                        else None
                    ),
                    items=tuple(items_by_held.get(hid, ())),
                )
            )
        return out

    def count_held_orders(self) -> int:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM held_orders WHERE status = ?;",
                (HELD_STATUS_HELD,),
            ).fetchone()
            return int(row["cnt"]) if row else 0

    def list_held_orders(
        self, search: str = "", *, actor: UserAccount | None = None
    ) -> list[HeldOrderRecord]:
        with self._tx() as conn:
            self._resolve_actor(conn, actor, Permission.CREATE_ORDER)
            return self._load_held_orders_in_tx(conn, status=HELD_STATUS_HELD, search=search)

    def get_held_order(
        self, held_order_id: int, *, actor: UserAccount | None = None
    ) -> HeldOrderRecord | None:
        with self._tx() as conn:
            self._resolve_actor(conn, actor, Permission.CREATE_ORDER)
            found = self._load_held_orders_in_tx(
                conn, held_order_id=int(held_order_id), status=None
            )
            return found[0] if found else None

    def create_or_update_held_order(
        self,
        lines: list[CartLine],
        *,
        order_type: str = ORDER_TYPE_DINE_IN,
        table_number: str = "",
        customer_name: str = "",
        customer_phone: str = "",
        delivery_address: str = "",
        order_notes: str = "",
        held_order_id: int | None = None,
        actor: UserAccount | None = None,
    ) -> HeldOrderRecord:
        if not lines:
            raise ValueError(tr("backend.err.empty_cart"))

        eff_order_type = validate_order_type(order_type)
        clean_table = " ".join((table_number or "").strip().split())[:24] if eff_order_type == ORDER_TYPE_DINE_IN else ""
        clean_cust_name = " ".join((customer_name or "").strip().split())[:80] if eff_order_type == ORDER_TYPE_DELIVERY else ""
        clean_cust_phone = " ".join((customer_phone or "").strip().split())[:32] if eff_order_type == ORDER_TYPE_DELIVERY else ""
        clean_deliv_addr = " ".join((delivery_address or "").strip().split())[:200] if eff_order_type == ORDER_TYPE_DELIVERY else ""
        clean_notes = " ".join((order_notes or "").strip().split())[:200]
        now = _utc_now_iso()

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.CREATE_ORDER)
            resolved_lines = self._validate_and_resolve_cart_lines_in_tx(conn, lines)
            subtotal_minor = sum(rl.line_total_minor for rl in resolved_lines)
            item_count = sum(rl.quantity for rl in resolved_lines)

            settings_row = conn.execute(
                "SELECT currency, next_hold_seq FROM business_settings WHERE id = 1;"
            ).fetchone()
            currency = validate_currency(settings_row["currency"])
            cashier_user_id = live_actor.id if live_actor is not None else None
            cashier_snapshot = live_actor.label if live_actor is not None else ""

            if held_order_id is not None:
                existing = conn.execute(
                    "SELECT id, status, hold_number FROM held_orders WHERE id = ?;",
                    (int(held_order_id),),
                ).fetchone()
                if existing is None:
                    raise ValueError(tr("backend.err.held_order_not_found"))
                if existing["status"] != HELD_STATUS_HELD:
                    raise ValueError(
                        tr("backend.err.held_order_closed", num=existing["hold_number"])
                    )
                target_id = int(existing["id"])
                hold_number = str(existing["hold_number"])
                conn.execute(
                    """
                    UPDATE held_orders
                    SET order_type = ?, table_number = ?, customer_name = ?,
                        customer_phone = ?, delivery_address = ?, order_notes = ?,
                        currency = ?, subtotal_minor = ?, item_count = ?, updated_at = ?
                    WHERE id = ? AND status = ?;
                    """,
                    (
                        eff_order_type,
                        clean_table,
                        clean_cust_name,
                        clean_cust_phone,
                        clean_deliv_addr,
                        clean_notes,
                        currency,
                        subtotal_minor,
                        item_count,
                        now,
                        target_id,
                        HELD_STATUS_HELD,
                    ),
                )
                conn.execute(
                    "DELETE FROM held_order_items WHERE held_order_id = ?;",
                    (target_id,),
                )
                audit_action = "HELD_ORDER_UPDATED"
            else:
                seq = int(settings_row["next_hold_seq"] if "next_hold_seq" in settings_row.keys() else 101)
                hold_number = f"HLD-{seq}"
                conn.execute(
                    "UPDATE business_settings SET next_hold_seq = ? WHERE id = 1;",
                    (seq + 1,),
                )
                cur = conn.execute(
                    """
                    INSERT INTO held_orders (
                        hold_number, status, order_type, table_number,
                        customer_name, customer_phone, delivery_address, order_notes,
                        currency, subtotal_minor, item_count,
                        cashier_user_id, cashier_name_snapshot, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        hold_number,
                        HELD_STATUS_HELD,
                        eff_order_type,
                        clean_table,
                        clean_cust_name,
                        clean_cust_phone,
                        clean_deliv_addr,
                        clean_notes,
                        currency,
                        subtotal_minor,
                        item_count,
                        cashier_user_id,
                        cashier_snapshot,
                        now,
                        now,
                    ),
                )
                target_id = int(cur.lastrowid)
                audit_action = "ORDER_HELD"

            for line in resolved_lines:
                conn.execute(
                    """
                    INSERT INTO held_order_items (
                        held_order_id, product_id, product_name, product_name_en, product_name_ar,
                        category_snapshot, image_filename, base_unit_price_minor, unit_price_minor,
                        quantity, modifiers_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        target_id,
                        line.product_id,
                        line.name,
                        line.name_en,
                        line.name_ar,
                        line.category,
                        line.image_filename,
                        line.resolved_base_unit_price_minor,
                        line.unit_price_minor,
                        line.quantity,
                        self._serialize_modifiers_json(line.modifiers),
                    ),
                )

            if live_actor is not None:
                self._write_audit(
                    conn,
                    audit_action,
                    actor=live_actor,
                    target_type="held_order",
                    target_id=hold_number,
                    details=(
                        f"{audit_action} {hold_number} [{eff_order_type}] "
                        f"({item_count} items, minor={subtotal_minor})"
                    ),
                )

            loaded = self._load_held_orders_in_tx(conn, held_order_id=target_id, status=None)
            assert loaded
            return loaded[0]

    def cancel_held_order(
        self, held_order_id: int, *, actor: UserAccount | None = None
    ) -> HeldOrderRecord:
        now = _utc_now_iso()
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.CREATE_ORDER)
            existing = conn.execute(
                "SELECT id, status, hold_number, subtotal_minor FROM held_orders WHERE id = ?;",
                (int(held_order_id),),
            ).fetchone()
            if existing is None:
                raise ValueError(tr("backend.err.held_order_not_found"))
            if existing["status"] != HELD_STATUS_HELD:
                raise ValueError(
                    tr("backend.err.held_order_closed", num=existing["hold_number"])
                )
            cur = conn.execute(
                """
                UPDATE held_orders
                SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?;
                """,
                (HELD_STATUS_CANCELLED, now, int(held_order_id), HELD_STATUS_HELD),
            )
            if cur.rowcount != 1:
                raise ValueError(
                    tr("backend.err.held_order_closed", num=existing["hold_number"])
                )
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "HELD_ORDER_CANCELLED",
                    actor=live_actor,
                    target_type="held_order",
                    target_id=str(existing["hold_number"]),
                    details=f"Cancelled held order {existing['hold_number']} (minor={existing['subtotal_minor']})",
                )
            loaded = self._load_held_orders_in_tx(
                conn, held_order_id=int(held_order_id), status=None
            )
            assert loaded
            return loaded[0]

    # ---------------- Orders, Payments & Cashier Attribution ----------------

    @staticmethod
    def _load_order_in_tx(conn: sqlite3.Connection, order_id: int) -> OrderRecord | None:
        row = conn.execute("SELECT * FROM orders WHERE id = ?;", (int(order_id),)).fetchone()
        if row is None:
            return None
        rkeys = row.keys()
        row_cashier_id = (
            int(row["cashier_user_id"])
            if "cashier_user_id" in rkeys and row["cashier_user_id"] is not None
            else None
        )
        item_rows = conn.execute(
            """
            SELECT id, product_id, product_name, unit_price_minor, quantity, line_total_minor,
                   product_name_en, product_name_ar, category_snapshot, base_unit_price_minor,
                   discount_minor, discount_type, discount_value,
                   estimated_unit_cost_minor, has_cost_data
            FROM order_items
            WHERE order_id = ?
            ORDER BY id ASC;
            """,
            (int(order_id),),
        ).fetchall()
        item_ids = [int(ir["id"]) for ir in item_rows]
        mods_by_item: dict[int, list[OrderItemModifierRecord]] = {}
        if item_ids:
            placeholders = ",".join("?" for _ in item_ids)
            mod_rows = conn.execute(
                f"""
                SELECT * FROM order_item_modifiers
                WHERE order_item_id IN ({placeholders})
                ORDER BY id ASC;
                """,
                item_ids,
            ).fetchall()
            for mr in mod_rows:
                oid_key = int(mr["order_item_id"])
                mods_by_item.setdefault(oid_key, []).append(
                    OrderItemModifierRecord(
                        id=int(mr["id"]),
                        order_item_id=oid_key,
                        modifier_group_id=(
                            int(mr["modifier_group_id"])
                            if mr["modifier_group_id"] is not None
                            else None
                        ),
                        modifier_option_id=(
                            int(mr["modifier_option_id"])
                            if mr["modifier_option_id"] is not None
                            else None
                        ),
                        group_name_snapshot=mr["group_name_snapshot"] or "",
                        group_name_en=mr["group_name_en"] or "",
                        group_name_ar=mr["group_name_ar"] or "",
                        option_name_snapshot=mr["option_name_snapshot"] or "",
                        option_name_en=mr["option_name_en"] or "",
                        option_name_ar=mr["option_name_ar"] or "",
                        price_delta_minor=int(mr["price_delta_minor"]),
                    )
                )

        items: list[OrderItemRecord] = []
        for ir in item_rows:
            ir_keys = ir.keys()
            item_mods = tuple(mods_by_item.get(int(ir["id"]), ()))
            raw_base = (
                int(ir["base_unit_price_minor"])
                if "base_unit_price_minor" in ir_keys
                else 0
            )
            if raw_base == 0 and int(ir["unit_price_minor"]) > 0 and not item_mods:
                raw_base = int(ir["unit_price_minor"])
            items.append(
                OrderItemRecord(
                    id=int(ir["id"]),
                    product_id=int(ir["product_id"]) if ir["product_id"] is not None else None,
                    product_name=ir["product_name"],
                    unit_price_minor=int(ir["unit_price_minor"]),
                    quantity=int(ir["quantity"]),
                    line_total_minor=int(ir["line_total_minor"]),
                    product_name_en=(
                        (ir["product_name_en"] if "product_name_en" in ir_keys else "") or ""
                    ),
                    product_name_ar=(
                        (ir["product_name_ar"] if "product_name_ar" in ir_keys else "") or ""
                    ),
                    category_snapshot=(
                        (ir["category_snapshot"] if "category_snapshot" in ir_keys else "") or ""
                    ),
                    base_unit_price_minor=raw_base,
                    modifiers=item_mods,
                    discount_minor=int(ir["discount_minor"]) if "discount_minor" in ir_keys else 0,
                    discount_type=(ir["discount_type"] if "discount_type" in ir_keys else "") or "",
                    discount_value=int(ir["discount_value"]) if "discount_value" in ir_keys else 0,
                    estimated_cost_minor=(
                        int(ir["estimated_unit_cost_minor"]) if "estimated_unit_cost_minor" in ir_keys else 0
                    ),
                    has_cost_data=bool(ir["has_cost_data"]) if "has_cost_data" in ir_keys else False,
                )
            )
        pay_rows = conn.execute(
            "SELECT * FROM payments WHERE order_id = ? ORDER BY id ASC;",
            (int(order_id),),
        ).fetchall()
        payments = tuple(_row_to_payment(pr) for pr in pay_rows)

        refund_rows = conn.execute(
            "SELECT * FROM order_refunds WHERE order_id = ? ORDER BY id ASC;",
            (int(order_id),),
        ).fetchall()
        refunds_list: list[OrderRefundRecord] = []
        if refund_rows:
            rf_ids = [int(rr["id"]) for rr in refund_rows]
            rf_ph = ",".join("?" for _ in rf_ids)
            ri_rows = conn.execute(
                f"SELECT * FROM order_refund_items WHERE refund_id IN ({rf_ph}) ORDER BY id ASC;",
                rf_ids,
            ).fetchall()
            ri_by_rf: dict[int, list[OrderRefundItemRecord]] = {}
            for rir in ri_rows:
                rid_k = int(rir["refund_id"])
                ri_by_rf.setdefault(rid_k, []).append(
                    OrderRefundItemRecord(
                        id=int(rir["id"]),
                        refund_id=rid_k,
                        order_item_id=int(rir["order_item_id"]),
                        product_id=int(rir["product_id"]) if rir["product_id"] is not None else None,
                        product_name=str(rir["product_name"] or ""),
                        quantity=int(rir["quantity"]),
                        refund_amount_minor=int(rir["refund_amount_minor"]),
                        stock_action=str(rir["stock_action"] or "NONE"),
                    )
                )
            for rr in refund_rows:
                rrk = rr.keys()
                rid = int(rr["id"])
                refunds_list.append(
                    OrderRefundRecord(
                        id=rid,
                        refund_number=str(rr["refund_number"] or f"RFD-{rid}"),
                        order_id=int(rr["order_id"]),
                        shift_id=int(rr["shift_id"]) if rr["shift_id"] is not None else None,
                        refund_type=str(rr["refund_type"] or "PARTIAL"),
                        refund_method=str(rr["refund_method"] or "CASH"),
                        refund_amount_minor=int(rr["refund_amount_minor"]),
                        currency=str(rr["currency"] or row["currency"]),
                        reason=str(rr["reason"] or ""),
                        refund_status=str(rr["refund_status"] or "CASH_RETURNED"),
                        external_reference=str(rr["external_reference"] or ""),
                        processed_by_user_id=(
                            int(rr["actor_user_id"])
                            if "actor_user_id" in rrk and rr["actor_user_id"] is not None
                            else None
                        ),
                        processed_by_name=str(rr["actor_name"] if "actor_name" in rrk else "") or "",
                        created_at=str(rr["created_at"] or ""),
                        items=tuple(ri_by_rf.get(rid, ())),
                    )
                )

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
            cashier_user_id=row_cashier_id,
            cashier_name_snapshot=(
                row["cashier_name_snapshot"]
                if "cashier_name_snapshot" in rkeys and row["cashier_name_snapshot"]
                else ""
            ),
            receipt_number=(
                (row["receipt_number"] if "receipt_number" in rkeys else "")
                or row["order_number"]
            ),
            tax_minor=int(row["tax_minor"]) if "tax_minor" in rkeys else 0,
            discount_minor=int(row["discount_minor"]) if "discount_minor" in rkeys else 0,
            payments=payments,
            order_type=(
                (row["order_type"] if "order_type" in rkeys else "") or ORDER_TYPE_DINE_IN
            ),
            table_number=(row["table_number"] if "table_number" in rkeys else "") or "",
            customer_name=(row["customer_name"] if "customer_name" in rkeys else "") or "",
            customer_phone=(row["customer_phone"] if "customer_phone" in rkeys else "") or "",
            delivery_address=(row["delivery_address"] if "delivery_address" in rkeys else "") or "",
            order_notes=(row["order_notes"] if "order_notes" in rkeys else "") or "",
            shift_id=int(row["shift_id"]) if ("shift_id" in rkeys and row["shift_id"] is not None) else None,
            discount_type=(row["discount_type"] if "discount_type" in rkeys else "") or "",
            discount_value=int(row["discount_value"]) if "discount_value" in rkeys else 0,
            discount_label=(row["discount_label"] if "discount_label" in rkeys else "") or "",
            refunded_total_minor=int(row["refunded_total_minor"]) if "refunded_total_minor" in rkeys else 0,
            refunds=tuple(refunds_list),
        )

    def create_order_with_payment(
        self,
        lines: list[CartLine],
        *,
        payment_method: str = METHOD_CASH,
        cash_received_minor: int = 0,
        payment_provider: str = "",
        custom_method_label: str = "",
        external_reference: str = "",
        confirmed_external: bool = False,
        reference_required: bool = False,
        split_payments: list[dict[str, object]] | None = None,
        tax_minor: int = 0,
        discount_minor: int = 0,
        discount_type: str = DISCOUNT_TYPE_NONE,
        discount_value: int = 0,
        discount_label: str = "",
        client_tx_key: str = "",
        order_type: str = ORDER_TYPE_DINE_IN,
        table_number: str = "",
        customer_name: str = "",
        customer_phone: str = "",
        delivery_address: str = "",
        order_notes: str = "",
        held_order_id: int | None = None,
        actor: UserAccount | None = None,
    ) -> OrderRecord:
        if not lines:
            raise ValueError(tr("backend.err.empty_cart"))
        if not isinstance(tax_minor, int) or tax_minor < 0:
            raise ValueError(tr("backend.err.price_non_neg"))
        if not isinstance(discount_minor, int) or discount_minor < 0:
            raise ValueError(tr("backend.err.price_non_neg"))

        eff_discount_type = (discount_type or "").strip().upper()
        if eff_discount_type not in VALID_DISCOUNT_TYPES:
            eff_discount_type = DISCOUNT_TYPE_NONE
        eff_discount_val = max(0, int(discount_value or 0))
        clean_discount_label = " ".join((discount_label or "").strip().split())[:60]

        eff_order_type = validate_order_type(order_type)
        clean_table = " ".join((table_number or "").strip().split())[:24] if eff_order_type == ORDER_TYPE_DINE_IN else ""
        clean_cust_name = " ".join((customer_name or "").strip().split())[:80] if eff_order_type == ORDER_TYPE_DELIVERY else ""
        clean_cust_phone = " ".join((customer_phone or "").strip().split())[:32] if eff_order_type == ORDER_TYPE_DELIVERY else ""
        clean_deliv_addr = " ".join((delivery_address or "").strip().split())[:200] if eff_order_type == ORDER_TYPE_DELIVERY else ""
        clean_notes = " ".join((order_notes or "").strip().split())[:200]

        tx_key = (client_tx_key or "").strip()[:80]
        now = _utc_now_iso()

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.CREATE_ORDER)

            # Idempotency protection: duplicate submission with the same client_tx_key returns existing order
            if tx_key:
                dup = conn.execute(
                    "SELECT id FROM orders WHERE client_tx_key = ? LIMIT 1;", (tx_key,)
                ).fetchone()
                if dup is not None:
                    existing_order = self._load_order_in_tx(conn, int(dup["id"]))
                    if existing_order is not None:
                        return existing_order

            # Verify held order is still open BEFORE consuming sequence numbers
            held_number_snap = ""
            if held_order_id is not None:
                hrow = conn.execute(
                    "SELECT status, hold_number FROM held_orders WHERE id = ?;",
                    (int(held_order_id),),
                ).fetchone()
                if hrow is None:
                    raise ValueError(tr("backend.err.held_order_not_found"))
                if hrow["status"] != HELD_STATUS_HELD:
                    raise ValueError(
                        tr("backend.err.held_order_closed", num=hrow["hold_number"])
                    )
                held_number_snap = str(hrow["hold_number"])

            settings_row = conn.execute(
                """
                SELECT business_name, address, phone, currency, next_order_seq, business_country,
                       require_shift_for_sales, allow_cashier_discounts, max_cashier_discount_pct
                FROM business_settings WHERE id = 1;
                """
            ).fetchone()
            skeys = settings_row.keys()

            active_shift_id: int | None = None
            if live_actor is not None:
                sh_row = conn.execute(
                    "SELECT id FROM cashier_shifts WHERE user_id = ? AND status = 'OPEN' ORDER BY id DESC LIMIT 1;",
                    (live_actor.id,),
                ).fetchone()
                if sh_row is not None:
                    active_shift_id = int(sh_row["id"])
                req_shift = bool(int(settings_row["require_shift_for_sales"] or 0)) if "require_shift_for_sales" in skeys else False
                if req_shift and active_shift_id is None:
                    raise ValueError(tr("backend.err.shift_required_for_sale"))

            resolved_lines = self._validate_and_resolve_cart_lines_in_tx(conn, lines)
            subtotal_minor = sum(rl.line_total_minor for rl in resolved_lines)

            if eff_discount_type == DISCOUNT_TYPE_PERCENT:
                pct = min(100, eff_discount_val)
                eff_discount_minor = min(subtotal_minor, (subtotal_minor * pct + 50) // 100)
                eff_discount_val = pct
            elif eff_discount_type == DISCOUNT_TYPE_FIXED:
                eff_discount_minor = min(subtotal_minor, eff_discount_val)
            else:
                eff_discount_minor = min(subtotal_minor, discount_minor)
                if eff_discount_minor > 0:
                    eff_discount_type = DISCOUNT_TYPE_FIXED
                    eff_discount_val = eff_discount_minor

            if eff_discount_minor > 0 and live_actor is not None and not live_actor.is_admin:
                allow_cashier_disc = bool(int(settings_row["allow_cashier_discounts"] or 0)) if "allow_cashier_discounts" in skeys else True
                if not allow_cashier_disc:
                    raise AuthorizationError(tr("backend.err.discount_not_allowed"))
                max_pct = validate_discount_pct_cap(
                    settings_row["max_cashier_discount_pct"] if "max_cashier_discount_pct" in skeys else 15
                )
                if eff_discount_type == DISCOUNT_TYPE_PERCENT and eff_discount_val > max_pct:
                    raise AuthorizationError(tr("backend.err.discount_exceeds_cap", max_pct=max_pct))
                if eff_discount_type == DISCOUNT_TYPE_FIXED and subtotal_minor > 0:
                    if (eff_discount_minor * 100) > (subtotal_minor * max_pct):
                        raise AuthorizationError(tr("backend.err.discount_exceeds_cap", max_pct=max_pct))

            total_minor = max(0, subtotal_minor + tax_minor - eff_discount_minor)

            validated_legs: list[tuple[ValidatedPaymentPayload, int, int]] = []
            if split_payments:
                sum_alloc = 0
                sum_non_cash = 0
                sum_cash_received = 0
                sum_change = 0
                for raw_leg in split_payments:
                    leg_amt = int(raw_leg.get("amount_minor") or 0)
                    if leg_amt < 0:
                        raise ValueError(tr("backend.err.payment_amount_negative"))
                    if leg_amt == 0 and len(split_payments) > 1:
                        continue
                    l_method = str(raw_leg.get("payment_method") or METHOD_CASH).strip().upper()
                    l_cash_rec = (
                        int(raw_leg.get("cash_received_minor", leg_amt) or 0)
                        if l_method == METHOD_CASH
                        else 0
                    )
                    v_leg = validate_payment_payload(
                        total_minor=leg_amt,
                        payment_method=l_method,
                        payment_amount_minor=leg_amt,
                        cash_received_minor=l_cash_rec,
                        payment_provider=str(raw_leg.get("payment_provider") or ""),
                        custom_method_label=str(raw_leg.get("custom_method_label") or ""),
                        external_reference=str(raw_leg.get("external_reference") or ""),
                        confirmed_external=bool(raw_leg.get("confirmed_external", False)),
                        reference_required=bool(raw_leg.get("reference_required", False)),
                    )
                    sum_alloc += leg_amt
                    if v_leg.payment_method != METHOD_CASH:
                        sum_non_cash += leg_amt
                    else:
                        sum_cash_received += v_leg.cash_received_minor
                        sum_change += v_leg.change_minor
                    validated_legs.append((v_leg, v_leg.cash_received_minor, v_leg.change_minor))

                if not validated_legs:
                    raise ValueError(tr("backend.err.payment_insufficient"))
                if sum_non_cash > total_minor:
                    raise ValueError(tr("backend.err.non_cash_overpayment"))
                if sum_alloc < total_minor:
                    raise ValueError(tr("backend.err.payment_insufficient"))
                if sum_alloc > total_minor:
                    raise ValueError(tr("backend.err.non_cash_overpayment"))
                order_cash_received = sum_cash_received
                order_change = sum_change
            else:
                validated_pay = validate_payment_payload(
                    total_minor=total_minor,
                    payment_method=payment_method,
                    payment_amount_minor=total_minor,
                    cash_received_minor=cash_received_minor,
                    payment_provider=payment_provider,
                    custom_method_label=custom_method_label,
                    external_reference=external_reference,
                    confirmed_external=confirmed_external,
                    reference_required=reference_required,
                )
                validated_legs.append(
                    (validated_pay, validated_pay.cash_received_minor, validated_pay.change_minor)
                )
                order_cash_received = validated_pay.cash_received_minor
                order_change = validated_pay.change_minor

            cashier_user_id = live_actor.id if live_actor is not None else None
            cashier_snapshot = live_actor.label if live_actor is not None else ""

            seq = int(settings_row["next_order_seq"])
            order_number = f"ORD-{seq}"
            receipt_number = f"RCP-{seq}"
            currency = validate_currency(settings_row["currency"])
            biz_name = settings_row["business_name"]
            biz_addr = settings_row["address"] or ""
            biz_phone = settings_row["phone"] or ""
            biz_country = validate_country_code(
                settings_row["business_country"]
                if "business_country" in skeys
                else DEFAULT_BUSINESS_COUNTRY
            )

            conn.execute(
                "UPDATE business_settings SET next_order_seq = ? WHERE id = 1;",
                (seq + 1,),
            )

            cur = conn.execute(
                """
                INSERT INTO orders (
                    order_number, created_at, currency, subtotal_minor, total_minor,
                    cash_received_minor, change_minor, business_name, business_address, business_phone,
                    cashier_user_id, cashier_name_snapshot,
                    receipt_number, tax_minor, discount_minor, client_tx_key, business_country_snapshot,
                    order_type, table_number, customer_name, customer_phone, delivery_address, order_notes,
                    shift_id, discount_type, discount_value, discount_label
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    order_number,
                    now,
                    currency,
                    subtotal_minor,
                    total_minor,
                    order_cash_received,
                    order_change,
                    biz_name,
                    biz_addr,
                    biz_phone,
                    cashier_user_id,
                    cashier_snapshot,
                    receipt_number,
                    tax_minor,
                    eff_discount_minor,
                    tx_key,
                    biz_country,
                    eff_order_type,
                    clean_table,
                    clean_cust_name,
                    clean_cust_phone,
                    clean_deliv_addr,
                    clean_notes,
                    active_shift_id,
                    eff_discount_type,
                    eff_discount_val,
                    clean_discount_label,
                ),
            )
            order_id = int(cur.lastrowid)

            deduct_payload: list[tuple[int, int | None, int, tuple[int, ...]]] = []
            for line in resolved_lines:
                line_total = line.line_total_minor
                snap_en = (line.name_en or "").strip()
                snap_ar = (line.name_ar or "").strip()
                snap_cat = (line.category or "").strip()
                if line.product_id is not None and (not snap_en or not snap_ar or not snap_cat):
                    pr = conn.execute(
                        "SELECT name_en, name_ar, category FROM products WHERE id = ?;",
                        (int(line.product_id),),
                    ).fetchone()
                    if pr is not None:
                        if not snap_en:
                            snap_en = (pr["name_en"] or "").strip()
                        if not snap_ar:
                            snap_ar = (pr["name_ar"] or "").strip()
                        if not snap_cat:
                            snap_cat = (pr["category"] or "").strip()
                if not snap_en and detect_primary_script(line.name) != "ar":
                    snap_en = line.name
                if not snap_ar and detect_primary_script(line.name) == "ar":
                    snap_ar = line.name

                item_cur = conn.execute(
                    """
                    INSERT INTO order_items (
                        order_id, product_id, product_name, unit_price_minor, quantity, line_total_minor,
                        product_name_en, product_name_ar, category_snapshot, base_unit_price_minor
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        order_id,
                        line.product_id,
                        line.name,
                        line.unit_price_minor,
                        line.quantity,
                        line_total,
                        snap_en,
                        snap_ar,
                        snap_cat,
                        line.resolved_base_unit_price_minor,
                    ),
                )
                order_item_id = int(item_cur.lastrowid)
                mod_opt_ids: list[int] = []
                for mod in line.modifiers:
                    if mod.option_id is not None:
                        mod_opt_ids.append(int(mod.option_id))
                    conn.execute(
                        """
                        INSERT INTO order_item_modifiers (
                            order_item_id, modifier_group_id, modifier_option_id,
                            group_name_snapshot, group_name_en, group_name_ar,
                            option_name_snapshot, option_name_en, option_name_ar,
                            price_delta_minor
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            order_item_id,
                            mod.group_id,
                            mod.option_id,
                            mod.group_name,
                            mod.group_name_en,
                            mod.group_name_ar,
                            mod.option_name,
                            mod.option_name_en,
                            mod.option_name_ar,
                            max(0, int(mod.price_delta_minor)),
                        ),
                    )
                deduct_payload.append((order_item_id, line.product_id, line.quantity, tuple(mod_opt_ids)))

            self._compute_and_deduct_sale_inventory_in_tx(
                conn,
                order_id=order_id,
                order_number=order_number,
                order_items_payload=deduct_payload,
                now_iso=now,
                actor=live_actor,
            )

            for v_leg, _c_rec, _c_chg in validated_legs:
                conn.execute(
                    """
                    INSERT INTO payments (
                        order_id, payment_method, payment_provider, custom_method_label,
                        payment_amount_minor, currency, payment_status, external_reference,
                        confirmed_by_user_id, confirmed_by_name, confirmed_at, verification_source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        order_id,
                        v_leg.payment_method,
                        v_leg.payment_provider,
                        v_leg.custom_method_label,
                        v_leg.payment_amount_minor,
                        currency,
                        v_leg.payment_status,
                        v_leg.external_reference,
                        cashier_user_id,
                        cashier_snapshot,
                        now,
                        v_leg.verification_source,
                    ),
                )

            if active_shift_id is not None:
                srow_open = conn.execute(
                    "SELECT opening_cash_minor FROM cashier_shifts WHERE id = ?;",
                    (active_shift_id,),
                ).fetchone()
                if srow_open is not None:
                    sh_totals = self._compute_shift_totals_in_tx(
                        conn, active_shift_id, int(srow_open["opening_cash_minor"] or 0)
                    )
                    conn.execute(
                        "UPDATE cashier_shifts SET expected_cash_minor = ? WHERE id = ?;",
                        (sh_totals["expected_cash_minor"], active_shift_id),
                    )

            if held_order_id is not None:
                upd_held = conn.execute(
                    """
                    UPDATE held_orders
                    SET status = ?, completed_order_id = ?, updated_at = ?
                    WHERE id = ? AND status = ?;
                    """,
                    (HELD_STATUS_COMPLETED, order_id, now, int(held_order_id), HELD_STATUS_HELD),
                )
                if upd_held.rowcount != 1:
                    raise ValueError(
                        tr("backend.err.held_order_closed", num=held_number_snap or str(held_order_id))
                    )
                if live_actor is not None:
                    self._write_audit(
                        conn,
                        "HELD_ORDER_COMPLETED",
                        actor=live_actor,
                        target_type="held_order",
                        target_id=held_number_snap or str(held_order_id),
                        details=f"Completed held order {held_number_snap} as {order_number}",
                    )

            if live_actor is not None:
                pay_parts = []
                for v_leg, _c_rec, _c_chg in validated_legs:
                    pl = v_leg.payment_method
                    if v_leg.payment_provider:
                        pl = f"{pl}/{v_leg.payment_provider}"
                    elif v_leg.custom_method_label:
                        pl = f"{pl}/{v_leg.custom_method_label}"
                    pay_parts.append(pl)
                self._write_audit(
                    conn,
                    "ORDER_CREATED",
                    actor=live_actor,
                    target_type="order",
                    target_id=order_number,
                    details=(
                        f"Completed {order_number} / {receipt_number} "
                        f"[{eff_order_type}] ({len(resolved_lines)} lines, minor={total_minor}, pay={' + '.join(pay_parts)})"
                    ),
                )

            created_order = self._load_order_in_tx(conn, order_id)
            assert created_order is not None
            return created_order

    def create_order(
        self,
        lines: list[CartLine],
        cash_received_minor: int,
        *,
        actor: UserAccount | None = None,
    ) -> OrderRecord:
        return self.create_order_with_payment(
            lines,
            payment_method=METHOD_CASH,
            cash_received_minor=cash_received_minor,
            actor=actor,
        )

    def list_order_cashiers(self, *, actor: UserAccount | None = None) -> list[tuple[int | None, str]]:
        """Return distinct cashiers for Admin sales filtering (never fabricating legacy identities)."""
        with self._tx() as conn:
            self._resolve_actor(conn, actor, Permission.VIEW_ALL_SALES)
            user_rows = conn.execute(
                """
                SELECT DISTINCT id, username, display_name, role
                FROM users
                ORDER BY username COLLATE NOCASE ASC;
                """
            ).fetchall()
            has_legacy = (
                conn.execute("SELECT 1 FROM orders WHERE cashier_user_id IS NULL LIMIT 1;").fetchone()
                is not None
            )

        out: list[tuple[int | None, str]] = []
        for ur in user_rows:
            disp = (ur["display_name"] or "").strip() or ur["username"]
            out.append((int(ur["id"]), f"{disp} ({tr_role(ur['role'])})"))
        if has_legacy:
            out.append((None, tr("backend.legacy_unassigned")))
        return out

    def list_orders(
        self,
        search: str = "",
        date_filter: str = "all",
        *,
        payment_method: str = "all",
        cashier_user_id: int | None = None,
        legacy_only: bool = False,
        actor: UserAccount | None = None,
    ) -> list[OrderSummary]:
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.VIEW_OWN_SALES)

            clauses: list[str] = []
            params: list[object] = []

            if live_actor is not None and live_actor.role == ROLE_CASHIER:
                if legacy_only or (
                    cashier_user_id is not None and int(cashier_user_id) != live_actor.id
                ):
                    raise AuthorizationError(tr("backend.err.cashier_own_sales"))
                clauses.append("o.cashier_user_id = ?")
                params.append(live_actor.id)
            else:
                if legacy_only:
                    clauses.append("o.cashier_user_id IS NULL")
                elif cashier_user_id is not None:
                    clauses.append("o.cashier_user_id = ?")
                    params.append(int(cashier_user_id))

            pm_filter = (payment_method or "all").strip().upper()
            if pm_filter != "ALL":
                clauses.append(
                    "EXISTS (SELECT 1 FROM payments px WHERE px.order_id = o.id AND px.payment_method = ?)"
                )
                params.append(pm_filter)

            q = (search or "").strip()
            if q:
                clauses.append(
                    """
                    (o.order_number LIKE ?
                     OR o.receipt_number LIKE ?
                     OR o.cashier_name_snapshot LIKE ?
                     OR o.order_type LIKE ?
                     OR o.table_number LIKE ?
                     OR o.customer_name LIKE ?
                     OR o.customer_phone LIKE ?
                     OR COALESCE(p.payment_method, '') LIKE ?
                     OR COALESCE(p.payment_provider, '') LIKE ?
                     OR COALESCE(p.custom_method_label, '') LIKE ?
                     OR COALESCE(p.external_reference, '') LIKE ?
                     OR EXISTS (
                         SELECT 1 FROM order_items oi2
                         WHERE oi2.order_id = o.id
                           AND (oi2.product_name LIKE ? OR oi2.product_name_en LIKE ? OR oi2.product_name_ar LIKE ?)
                     ))
                    """
                )
                like = f"%{q}%"
                params.extend([like] * 14)

            where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            sql = f"""
                SELECT
                    o.id,
                    o.order_number,
                    o.receipt_number,
                    o.created_at,
                    o.currency,
                    o.total_minor,
                    o.cash_received_minor,
                    o.change_minor,
                    o.cashier_user_id,
                    o.cashier_name_snapshot,
                    o.order_type,
                    o.table_number,
                    o.customer_name,
                    COALESCE(o.discount_minor, 0) AS discount_minor,
                    COALESCE(o.refunded_total_minor, 0) AS refunded_total_minor,
                    o.shift_id,
                    (SELECT COUNT(*) FROM payments p2 WHERE p2.order_id = o.id) AS payment_count,
                    COALESCE(p.payment_method, 'CASH') AS payment_method,
                    COALESCE(p.payment_provider, '') AS payment_provider,
                    COALESCE(p.custom_method_label, '') AS custom_method_label,
                    COALESCE(p.payment_status, 'cash_settled') AS payment_status,
                    COALESCE(p.external_reference, '') AS external_reference,
                    COALESCE(SUM(oi.quantity), 0) AS item_count
                FROM orders o
                LEFT JOIN order_items oi ON oi.order_id = o.id
                LEFT JOIN payments p ON p.id = (
                    SELECT MIN(id) FROM payments WHERE order_id = o.id
                )
                {where_sql}
                GROUP BY o.id
                ORDER BY o.id DESC;
            """
            rows = conn.execute(sql, params).fetchall()

        summaries = [
            OrderSummary(
                id=int(r["id"]),
                order_number=r["order_number"],
                created_at=r["created_at"],
                currency=r["currency"],
                item_count=int(r["item_count"]),
                total_minor=int(r["total_minor"]),
                cash_received_minor=int(r["cash_received_minor"]),
                change_minor=int(r["change_minor"]),
                cashier_user_id=(
                    int(r["cashier_user_id"]) if r["cashier_user_id"] is not None else None
                ),
                cashier_name_snapshot=(r["cashier_name_snapshot"] or ""),
                receipt_number=(r["receipt_number"] or r["order_number"]),
                payment_method=r["payment_method"],
                payment_provider=r["payment_provider"],
                custom_method_label=r["custom_method_label"],
                payment_status=r["payment_status"],
                external_reference=r["external_reference"],
                order_type=r["order_type"] or ORDER_TYPE_DINE_IN,
                table_number=r["table_number"] or "",
                customer_name=r["customer_name"] or "",
                discount_minor=int(r["discount_minor"] or 0),
                refunded_total_minor=int(r["refunded_total_minor"] or 0),
                payment_count=max(1, int(r["payment_count"] or 1)),
                shift_id=int(r["shift_id"]) if r["shift_id"] is not None else None,
            )
            for r in rows
        ]

        df = (date_filter or "all").lower()
        if df == "all":
            return summaries

        now_local = datetime.now().astimezone()
        today_date = now_local.date()
        filtered: list[OrderSummary] = []
        for s in summaries:
            local_dt = _parse_iso_to_local(s.created_at)
            if local_dt is None:
                continue
            d = local_dt.date()
            if df == "today" and d == today_date:
                filtered.append(s)
            elif df == "week" and d >= (today_date - timedelta(days=6)):
                filtered.append(s)
            elif df == "month" and d >= (today_date - timedelta(days=29)):
                filtered.append(s)
        return filtered

    def get_order(
        self, order_id: int, *, actor: UserAccount | None = None
    ) -> OrderRecord | None:
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.VIEW_OWN_SALES)
            row = conn.execute("SELECT cashier_user_id FROM orders WHERE id = ?;", (int(order_id),)).fetchone()
            if row is None:
                return None

            row_cashier_id = (
                int(row["cashier_user_id"])
                if row["cashier_user_id"] is not None
                else None
            )
            if (
                live_actor is not None
                and live_actor.role == ROLE_CASHIER
                and row_cashier_id != live_actor.id
            ):
                raise AuthorizationError(tr("backend.err.cashier_own_receipt"))

            return self._load_order_in_tx(conn, int(order_id))

    # ---------------- SQLite Snapshots & Legacy Restore Helpers ----------------

    def create_consistent_sqlite_snapshot(self, dest_path: Path) -> None:
        """Write a transactionally consistent SQLite copy using the SQLite backup API."""
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.resolve() == self.db_path.resolve():
            raise ValueError(tr("backend.err.backup_dest_same"))

        src_conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
            src_conn.close()

    def backup_to_file(
        self, dest_path: Path, *, actor: UserAccount | None = None
    ) -> None:
        with self._tx() as conn:
            self._resolve_actor(conn, actor, Permission.MANAGE_BACKUPS)
        self.create_consistent_sqlite_snapshot(dest_path)

    @staticmethod
    def validate_backup_file(src_path: Path) -> int:
        """Validate SQLite integrity, required tables, and compatible schema version."""
        src = Path(src_path)
        if not src.exists() or not src.is_file():
            raise ValueError(tr("backend.err.backup_not_found"))
        if src.stat().st_size < 512:
            raise ValueError(tr("backend.err.backup_invalid_db"))

        try:
            conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise ValueError(tr("backend.err.backup_invalid_db")) from exc

        try:
            integrity = conn.execute("PRAGMA integrity_check;").fetchone()
            if not integrity or integrity[0] != "ok":
                raise ValueError(tr("backend.err.backup_corrupt", detail="PRAGMA integrity_check"))
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table';"
                ).fetchall()
            }
            missing = REQUIRED_TABLES - tables
            if missing:
                raise ValueError(tr("backend.err.backup_incompatible"))
            settings_count = conn.execute("SELECT COUNT(*) FROM business_settings;").fetchone()[0]
            if settings_count != 1:
                raise ValueError(tr("backend.err.backup_missing_biz"))

            ver_row = conn.execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version';"
            ).fetchone()
            schema_ver = 1
            if ver_row is not None and ver_row[0]:
                try:
                    schema_ver = int(ver_row[0])
                except ValueError as exc:
                    raise ValueError(tr("backend.err.backup_bad_ver_meta")) from exc
                if schema_ver < 1 or schema_ver > CURRENT_SCHEMA_VERSION:
                    raise ValueError(
                        tr(
                            "backend.err.backup_bad_schema",
                            ver=schema_ver,
                            max_ver=CURRENT_SCHEMA_VERSION,
                        )
                    )
            return schema_ver
        except sqlite3.Error as exc:
            raise ValueError(tr("backend.err.backup_invalid_db")) from exc
        finally:
            conn.close()

    def restore_from_file(
        self, src_path: Path, *, actor: UserAccount | None = None
    ) -> None:
        src = Path(src_path)
        if src.resolve() == self.db_path.resolve():
            raise ValueError(tr("backend.err.backup_restore_same"))
        with self._tx() as conn:
            self._resolve_actor(conn, actor, Permission.RESTORE_BACKUP)
        self.validate_backup_file(src)

        self.close()
        src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        dst_conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
        self._init_schema()

    def factory_reset(self, *, actor: UserAccount | None = None) -> None:
        with self._tx() as conn:
            self._resolve_actor(conn, actor, Permission.FACTORY_RESET)
            conn.executescript(
                """
                DELETE FROM order_refund_items;
                DELETE FROM order_refunds;
                DELETE FROM shift_cash_movements;
                DELETE FROM stock_movements;
                DELETE FROM modifier_option_recipes;
                DELETE FROM product_recipes;
                DELETE FROM ingredients;
                DELETE FROM expenses;
                DELETE FROM cashier_shifts;
                DELETE FROM order_item_modifiers;
                DELETE FROM held_order_items;
                DELETE FROM held_orders;
                DELETE FROM product_modifier_option_overrides;
                DELETE FROM product_modifier_groups;
                DELETE FROM modifier_options;
                DELETE FROM modifier_groups;
                DELETE FROM payments;
                DELETE FROM order_items;
                DELETE FROM orders;
                DELETE FROM products;
                DELETE FROM categories;
                DELETE FROM translation_cache;
                DELETE FROM audit_logs;
                DELETE FROM users;
                DELETE FROM business_settings;
                DELETE FROM app_meta;
                """
            )
        self._session_actor = None
        self._unknown_login_attempts.clear()
        self._init_schema()

    # ---------------- Admin Reports & Analytics ----------------

    def get_sales_analytics(
        self, date_filter: str = "week", *, actor: UserAccount | None = None
    ) -> SalesAnalyticsSummary:
        """Compute genuine sales metrics, trend series, category share, and top items from SQLite."""
        df = (date_filter or "week").lower()
        now_local = datetime.now().astimezone()
        today_date = now_local.date()

        with self._tx() as conn:
            self._resolve_actor(conn, actor, Permission.VIEW_REPORTS)
            order_rows = conn.execute(
                "SELECT id, created_at, total_minor FROM orders ORDER BY id ASC;"
            ).fetchall()
            item_rows = conn.execute(
                """
                SELECT
                    oi.order_id,
                    oi.product_name,
                    oi.quantity,
                    oi.line_total_minor,
                    COALESCE(
                        NULLIF(TRIM(oi.category_snapshot), ''),
                        NULLIF(TRIM(c.name_en), ''),
                        NULLIF(TRIM(c.name_ar), ''),
                        NULLIF(TRIM(p.category), ''),
                        'Other'
                    ) AS category_name
                FROM order_items oi
                LEFT JOIN products p ON p.id = oi.product_id
                LEFT JOIN categories c ON c.id = p.category_id
                ORDER BY oi.id ASC;
                """
            ).fetchall()

        items_by_order: dict[int, list[sqlite3.Row]] = {}
        for ir in item_rows:
            items_by_order.setdefault(int(ir["order_id"]), []).append(ir)

        today_revenue_minor = 0
        today_order_count = 0
        filtered_orders: list[tuple[int, datetime, int]] = []

        for row in order_rows:
            local_dt = _parse_iso_to_local(row["created_at"])
            if local_dt is None:
                continue
            d = local_dt.date()
            tot = int(row["total_minor"])
            if d == today_date:
                today_revenue_minor += tot
                today_order_count += 1

            include = (
                df == "all"
                or (df == "today" and d == today_date)
                or (df == "week" and d >= (today_date - timedelta(days=6)))
                or (df == "month" and d >= (today_date - timedelta(days=29)))
            )
            if include:
                filtered_orders.append((int(row["id"]), local_dt, tot))

        order_count = len(filtered_orders)
        total_revenue_minor = sum(o[2] for o in filtered_orders)
        average_order_minor = (total_revenue_minor // order_count) if order_count else 0

        cat_totals: dict[str, int] = {}
        cat_units: dict[str, int] = {}
        prod_units: dict[str, int] = {}
        prod_totals: dict[str, int] = {}
        total_items_sold = 0

        bucket_totals: dict[str, int] = {}
        bucket_counts: dict[str, int] = {}
        bucket_cat_totals: dict[str, dict[str, int]] = {}

        for oid, local_dt, tot in filtered_orders:
            bkey = f"{local_dt.hour:02d}:00" if df == "today" else local_dt.date().isoformat()
            bucket_totals[bkey] = bucket_totals.get(bkey, 0) + tot
            bucket_counts[bkey] = bucket_counts.get(bkey, 0) + 1
            cat_map = bucket_cat_totals.setdefault(bkey, {})

            for ir in items_by_order.get(oid, []):
                qty = int(ir["quantity"])
                line_tot = int(ir["line_total_minor"])
                cat = ir["category_name"]
                pname = ir["product_name"]

                total_items_sold += qty
                cat_totals[cat] = cat_totals.get(cat, 0) + line_tot
                cat_units[cat] = cat_units.get(cat, 0) + qty
                prod_units[pname] = prod_units.get(pname, 0) + qty
                prod_totals[pname] = prod_totals.get(pname, 0) + line_tot
                cat_map[cat] = cat_map.get(cat, 0) + line_tot

        category_sales = sorted(
            [
                CategorySalesPoint(category=c, total_minor=v, units_sold=cat_units[c])
                for c, v in cat_totals.items()
            ],
            key=lambda x: (-x.total_minor, -x.units_sold, x.category.lower()),
        )
        top_products = sorted(
            [
                ProductSalesPoint(product_name=n, units_sold=u, total_minor=prod_totals[n])
                for n, u in prod_units.items()
            ],
            key=lambda x: (-x.units_sold, -x.total_minor, x.product_name.lower()),
        )[:8]

        daily_points: list[DailySalesPoint] = []
        bucket_keys: list[str] = []

        if filtered_orders:
            if df == "today":
                hours = sorted({int(k.split(":")[0]) for k in bucket_totals} | {now_local.hour})
                min_h = max(0, min(hours) - 1)
                max_h = min(23, max(hours))
                for h in range(min_h, max_h + 1):
                    bk = f"{h:02d}:00"
                    bucket_keys.append(bk)
                    hour_dt = local_dt.replace(hour=h, minute=0)
                    lbl = hour_dt.strftime("%I %p").lstrip("0")
                    daily_points.append(
                        DailySalesPoint(
                            date_key=bk,
                            label=lbl,
                            total_minor=bucket_totals.get(bk, 0),
                            order_count=bucket_counts.get(bk, 0),
                        )
                    )
            else:
                if df == "week":
                    start_d = today_date - timedelta(days=6)
                    end_d = today_date
                elif df == "month":
                    start_d = today_date - timedelta(days=29)
                    end_d = today_date
                else:
                    dates = [o[1].date() for o in filtered_orders]
                    start_d = min(dates)
                    end_d = max(dates)
                    if (end_d - start_d).days > 60:
                        start_d = end_d - timedelta(days=59)

                cur_d = start_d
                while cur_d <= end_d:
                    bk = cur_d.isoformat()
                    bucket_keys.append(bk)
                    lbl = cur_d.strftime("%b %d").replace(" 0", " ")
                    daily_points.append(
                        DailySalesPoint(
                            date_key=bk,
                            label=lbl,
                            total_minor=bucket_totals.get(bk, 0),
                            order_count=bucket_counts.get(bk, 0),
                        )
                    )
                    cur_d += timedelta(days=1)

        top_cat_names = [cs.category for cs in category_sales[:3]]
        category_daily_series: dict[str, list[int]] = {
            cat_name: [
                bucket_cat_totals.get(bk, {}).get(cat_name, 0) for bk in bucket_keys
            ]
            for cat_name in top_cat_names
        }

        return SalesAnalyticsSummary(
            period=df,
            order_count=order_count,
            total_revenue_minor=total_revenue_minor,
            total_items_sold=total_items_sold,
            average_order_minor=average_order_minor,
            today_revenue_minor=today_revenue_minor,
            today_order_count=today_order_count,
            daily_points=daily_points,
            category_sales=category_sales,
            top_products=top_products,
            category_daily_series=category_daily_series,
        )
